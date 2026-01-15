import runpod
from runpod.serverless.utils import rp_upload
import json
import urllib.request
import urllib.parse
import time
import os
import requests
import base64
from io import BytesIO
import websocket
import uuid
import tempfile
import traceback

# Host where ComfyUI is running
COMFY_HOST = "127.0.0.1:8188"
WEBSOCKET_RECONNECT_ATTEMPTS = int(os.environ.get("WEBSOCKET_RECONNECT_ATTEMPTS", 5))
WEBSOCKET_RECONNECT_DELAY_S = int(os.environ.get("WEBSOCKET_RECONNECT_DELAY_S", 3))

def check_server(url, retries=50, delay=500):
    for i in range(retries):
        try:
            response = requests.get(url, timeout=5)
            if response.status_code == 200:
                return True
        except:
            pass
        time.sleep(delay / 1000)
    return False

def wait_for_node(node_name, timeout=120):
    print(f"worker-ffgo - Waiting for node '{node_name}' to load...")
    start = time.time()
    while time.time() - start < timeout:
        try:
            r = requests.get(f"http://{COMFY_HOST}/object_info", timeout=5)
            if r.status_code == 200:
                data = r.json()
                if node_name in data:
                    print(f"worker-ffgo - Node '{node_name}' loaded.")
                    return True
        except Exception:
            pass
        time.sleep(2)
    return False

def upload_images(images):
    if not images:
        return {"status": "success"}
    
    print(f"worker-ffgo - Uploading {len(images)} images...")
    for image in images:
        try:
            name = image["name"]
            image_data = image["image"]
            if "," in image_data:
                base64_data = image_data.split(",", 1)[1]
            else:
                base64_data = image_data
            
            blob = base64.b64decode(base64_data)
            files = {"image": (name, BytesIO(blob), "image/png"), "overwrite": (None, "true")}
            
            resp = requests.post(f"http://{COMFY_HOST}/upload/image", files=files, timeout=30)
            resp.raise_for_status()
            print(f"worker-ffgo - Uploaded {name}")
        except Exception as e:
            print(f"worker-ffgo - Error uploading {name}: {e}")
            return {"status": "error", "message": str(e)}
    return {"status": "success"}

def queue_workflow(workflow, client_id):
    payload = {"prompt": workflow, "client_id": client_id}
    data = json.dumps(payload).encode("utf-8")
    resp = requests.post(f"http://{COMFY_HOST}/prompt", data=data, headers={"Content-Type": "application/json"}, timeout=30)
    if resp.status_code != 200:
         raise ValueError(f"ComfyUI Error: {resp.text}")
    return resp.json()

def get_history(prompt_id):
    resp = requests.get(f"http://{COMFY_HOST}/history/{prompt_id}", timeout=30)
    resp.raise_for_status()
    return resp.json()

def get_image_data(filename, subfolder, image_type):
    data = {"filename": filename, "subfolder": subfolder, "type": image_type}
    query = urllib.parse.urlencode(data)
    resp = requests.get(f"http://{COMFY_HOST}/view?{query}", timeout=60)
    resp.raise_for_status()
    return resp.content

def handler(job):
    job_input = job["input"]
    job_id = job["id"]

    if not job_input:
        return {"error": "No input provided"}

    # Generic Handler: Expects 'workflow' and optional 'images'
    workflow = job_input.get("workflow")
    images_input = job_input.get("images")

    if not workflow:
        return {"error": "Missing 'workflow' in input"}
    
    # Check Server
    if not check_server(f"http://{COMFY_HOST}/"):
        return {"error": "ComfyUI server unreachable"}

    # Wait for critical Custom Nodes to load (Fixes Race Condition)
    if not wait_for_node("RMBG"):
        return {"error": "Timeout waiting for RMBG node to load. Custom nodes took too long."}

    # Upload Images
    if images_input:
        upload_res = upload_images(images_input)
        if upload_res.get("status") == "error":
            return {"error": "Image upload failed", "details": upload_res}

    # Connect WebSocket
    ws = None
    client_id = str(uuid.uuid4())
    
    try:
        ws = websocket.WebSocket()
        ws.connect(f"ws://{COMFY_HOST}/ws?clientId={client_id}", timeout=10)
        
        # Queue
        queue_resp = queue_workflow(workflow, client_id)
        prompt_id = queue_resp["prompt_id"]
        print(f"worker-ffgo - Queued: {prompt_id}")
        
        # Monitor
        while True:
            out = ws.recv()
            if isinstance(out, str):
                msg = json.loads(out)
                if msg["type"] == "executing":
                    data = msg["data"]
                    if data["node"] is None and data["prompt_id"] == prompt_id:
                        print("worker-ffgo - Finished")
                        break
                elif msg["type"] == "execution_error":
                     data = msg["data"]
                     if data["prompt_id"] == prompt_id:
                         raise ValueError(f"Execution error: {data.get('exception_message')}")

        # Result
        history = get_history(prompt_id)
        outputs = history[prompt_id]["outputs"]
        results = []
        
        for node_id, node_out in outputs.items():
            if "videos" in node_out:
                for vid in node_out["videos"]:
                    fname = vid["filename"]
                    data = get_image_data(fname, vid.get("subfolder",""), vid.get("type"))
                    
                    if os.environ.get("BUCKET_ENDPOINT_URL"):
                         with tempfile.NamedTemporaryFile(suffix=".mp4", delete=False) as tf:
                             tf.write(data)
                             tf_path = tf.name
                         s3_url = rp_upload.upload_image(job_id, tf_path)
                         os.remove(tf_path)
                         results.append({"type": "s3_url", "data": s3_url, "filename": fname})
                    else:
                        b64 = base64.b64encode(data).decode("utf-8")
                        results.append({"type": "base64", "data": b64, "filename": fname})
                        
        return {"output": results}

    except Exception as e:
        print(f"Error: {e}")
        return {"error": str(e)}
    finally:
        if ws:
            ws.close()

# Debug: Check Volume Mount
if os.path.exists("/workspace"):
    print("worker-ffgo - '/workspace' exists.")
    try:
        print(f"worker-ffgo - Contents of /workspace: {os.listdir('/workspace')}")
        # Check for deep nesting just in case
        if os.path.exists("/workspace/models"):
             print(f"worker-ffgo - Contents of /workspace/models: {os.listdir('/workspace/models')}")
             if os.path.exists("/workspace/models/models"):
                  print(f"worker-ffgo - Contents of /workspace/models/models: {os.listdir('/workspace/models/models')}")
    except Exception as e:
        print(f"worker-ffgo - Error listing volume: {e}")
else:
    print("worker-ffgo - WARNING: '/workspace' does NOT exist. Check mount path in Template.")

runpod.serverless.start({"handler": handler})
