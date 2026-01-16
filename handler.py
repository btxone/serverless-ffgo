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

# ==========================================
# CONFIGURATION
# ==========================================
COMFY_HOST = "127.0.0.1:8188"
WEBSOCKET_RECONNECT_ATTEMPTS = int(os.environ.get("WEBSOCKET_RECONNECT_ATTEMPTS", 5))
WEBSOCKET_RECONNECT_DELAY_S = int(os.environ.get("WEBSOCKET_RECONNECT_DELAY_S", 3))

# Network Volume Path for Serverless (Verification)
NETWORK_VOLUME_PATH = "/runpod-volume"

# ==========================================
# HELPER FUNCTIONS
# ==========================================

def check_server(url, retries=50, delay=500):
    """Checks if ComfyUI is reachable."""
    for i in range(retries):
        try:
            response = requests.get(url, timeout=5)
            if response.status_code == 200:
                return True
        except:
            pass
        time.sleep(delay / 1000)
    return False

def wait_for_node(node_name, timeout=180):
    """
    Waits for a specific custom node to be loaded by ComfyUI.
    Increased timeout for heavy models like Wan2.1
    """
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
    """Uploads Base64 images to ComfyUI input directory."""
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
    """Submits the workflow to the queue."""
    payload = {"prompt": workflow, "client_id": client_id}
    data = json.dumps(payload).encode("utf-8")
    resp = requests.post(f"http://{COMFY_HOST}/prompt", data=data, headers={"Content-Type": "application/json"}, timeout=30)
    if resp.status_code != 200:
         raise ValueError(f"ComfyUI Error: {resp.text}")
    return resp.json()

def get_history(prompt_id):
    """Retrieves execution history/results."""
    resp = requests.get(f"http://{COMFY_HOST}/history/{prompt_id}", timeout=30)
    resp.raise_for_status()
    return resp.json()

def get_image_data(filename, subfolder, image_type):
    """Downloads the generated image/video from ComfyUI."""
    data = {"filename": filename, "subfolder": subfolder, "type": image_type}
    query = urllib.parse.urlencode(data)
    resp = requests.get(f"http://{COMFY_HOST}/view?{query}", timeout=60)
    resp.raise_for_status()
    return resp.content

# ==========================================
# MAIN HANDLER
# ==========================================

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
    
    # 1. Check Server Availability
    if not check_server(f"http://{COMFY_HOST}/"):
        return {"error": "ComfyUI server unreachable after retries"}

    # 2. Wait for Critical Nodes
    if not wait_for_node("RMBG"):
        return {"error": "Timeout waiting for RMBG node to load."}

    # 3. Upload Input Images (if any)
    if images_input:
        upload_res = upload_images(images_input)
        if upload_res.get("status") == "error":
            return {"error": "Image upload failed", "details": upload_res}

    # 4. Connect WebSocket & Execute
    ws = None
    client_id = str(uuid.uuid4())
    
    try:
        ws = websocket.WebSocket()
        # === CORRECCIÓN AQUÍ: Timeout aumentado a 300 segundos ===
        ws.connect(f"ws://{COMFY_HOST}/ws?clientId={client_id}", timeout=300)
        
        # Queue Workflow
        queue_resp = queue_workflow(workflow, client_id)
        prompt_id = queue_resp["prompt_id"]
        print(f"worker-ffgo - Queued: {prompt_id}")
        
        # Monitor Execution via WebSocket
        while True:
            # El recv esperará hasta 300 segundos por un mensaje nuevo
            out = ws.recv()
            if isinstance(out, str):
                msg = json.loads(out)
                
                if msg["type"] == "executing":
                    data = msg["data"]
                    if data["node"] is None and data["prompt_id"] == prompt_id:
                        print("worker-ffgo - Finished execution")
                        break
                elif msg["type"] == "execution_error":
                     data = msg["data"]
                     if data["prompt_id"] == prompt_id:
                         raise ValueError(f"Execution error: {data.get('exception_message')}")

        # 5. Retrieve Results
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
            
            elif "images" in node_out:
                for img in node_out["images"]:
                    fname = img["filename"]
                    data = get_image_data(fname, img.get("subfolder",""), img.get("type"))
                    b64 = base64.b64encode(data).decode("utf-8")
                    results.append({"type": "base64", "data": b64, "filename": fname})

        return {"output": results}

    except Exception as e:
        print(f"Error executing workflow: {e}")
        traceback.print_exc()
        return {"error": str(e)}
    finally:
        if ws:
            ws.close()

# ==========================================
# SYSTEM STARTUP CHECK
# ==========================================
if os.path.exists(NETWORK_VOLUME_PATH):
    print(f"worker-ffgo - '{NETWORK_VOLUME_PATH}' detected.")
else:
    print(f"worker-ffgo - WARNING: '{NETWORK_VOLUME_PATH}' does NOT exist.")

runpod.serverless.start({"handler": handler})