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
import socket
import traceback
import random

# Time to wait between API check attempts in milliseconds
COMFY_API_AVAILABLE_INTERVAL_MS = 50
# Maximum number of API check attempts
COMFY_API_AVAILABLE_MAX_RETRIES = 500
# Websocket reconnection behaviour
WEBSOCKET_RECONNECT_ATTEMPTS = int(os.environ.get("WEBSOCKET_RECONNECT_ATTEMPTS", 5))
WEBSOCKET_RECONNECT_DELAY_S = int(os.environ.get("WEBSOCKET_RECONNECT_DELAY_S", 3))

# Host where ComfyUI is running
COMFY_HOST = "127.0.0.1:8188"

# Load the FFGO Workflow Template once at startup
WORKFLOW_TEMPLATE = None
try:
    with open("/workflow_ffgo.json", "r", encoding="utf-8") as f:
        WORKFLOW_TEMPLATE = json.load(f)
    print("worker-ffgo - Loaded workflow template")
except Exception as e:
    print(f"worker-ffgo - Error loading workflow template: {e}")

# ---------------------------------------------------------------------------
# Helper functions (same as original with minor connection helpers)
# ---------------------------------------------------------------------------

def _comfy_server_status():
    try:
        resp = requests.get(f"http://{COMFY_HOST}/", timeout=5)
        return {"reachable": resp.status_code == 200, "status_code": resp.status_code}
    except Exception as exc:
        return {"reachable": False, "error": str(exc)}

def _attempt_websocket_reconnect(ws_url, max_attempts, delay_s, initial_error):
    print(f"worker-ffgo - Websocket connection closed unexpectedly: {initial_error}. Attempting to reconnect...")
    last_reconnect_error = initial_error
    for attempt in range(max_attempts):
        srv_status = _comfy_server_status()
        if not srv_status["reachable"]:
            print(f"worker-ffgo - ComfyUI HTTP unreachable – aborting websocket reconnect")
            raise websocket.WebSocketConnectionClosedException("ComfyUI HTTP unreachable during websocket reconnect")
        
        print(f"worker-ffgo - Reconnect attempt {attempt + 1}/{max_attempts}...")
        try:
            new_ws = websocket.WebSocket()
            new_ws.connect(ws_url, timeout=10)
            print(f"worker-ffgo - Websocket reconnected successfully.")
            return new_ws
        except Exception as reconn_err:
            last_reconnect_error = reconn_err
            print(f"worker-ffgo - Reconnect attempt {attempt + 1} failed: {reconn_err}")
            time.sleep(delay_s)
    
    raise websocket.WebSocketConnectionClosedException(f"Failed to reconnect. Last error: {last_reconnect_error}")

def check_server(url, retries=500, delay=50):
    print(f"worker-ffgo - Checking API server at {url}...")
    for i in range(retries):
        try:
            response = requests.get(url, timeout=5)
            if response.status_code == 200:
                print(f"worker-ffgo - API is reachable")
                return True
        except Exception:
            pass
        time.sleep(delay / 1000)
    return False

def upload_images(images):
    """
    Upload images to ComfyUI.
    Expects 'images' to be a list of {'name': 'filename.png', 'image': 'base64str...'}
    """
    if not images:
        return {"status": "success", "message": "No images to upload"}

    print(f"worker-ffgo - Uploading {len(images)} image(s)...")
    upload_errors = []

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
            print(f"worker-ffgo - Successfully uploaded {name}")
        except Exception as e:
            msg = f"Error uploading {image.get('name')}: {e}"
            print(f"worker-ffgo - {msg}")
            upload_errors.append(msg)
            
    if upload_errors:
        return {"status": "error", "message": "Upload failed", "details": upload_errors}
    
    return {"status": "success"}

def queue_workflow(workflow, client_id):
    payload = {"prompt": workflow, "client_id": client_id}
    data = json.dumps(payload).encode("utf-8")
    resp = requests.post(f"http://{COMFY_HOST}/prompt", data=data, headers={"Content-Type": "application/json"}, timeout=30)
    if resp.status_code != 200:
        raise ValueError(f"ComfyUI returned error: {resp.text}")
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

# ---------------------------------------------------------------------------
# FFGO Handler Logic
# ---------------------------------------------------------------------------

def handler(job):
    job_input = job["input"]
    job_id = job["id"]

    # 1. Validation
    if not job_input:
        return {"error": "No input provided"}
    
    # We expect parameters for the FFGO workflow
    # - prompt (positive)
    # - negative_prompt (optional)
    # - images (list of 3 base64 images, or we map them if names match)
    # - seed (optional)
    
    prompt_text = job_input.get("prompt")
    negative_text = job_input.get("negative_prompt", "low quality, blurry")
    images_input = job_input.get("images") # Must be list of {name, image}
    seed = job_input.get("seed", random.randint(1, 999999999999999))
    
    if not prompt_text:
        return {"error": "Missing 'prompt' parameter"}
    if not images_input or not isinstance(images_input, list) or len(images_input) < 1:
        return {"error": "Missing or invalid 'images' parameter. At least 1 image required."}

    # 2. Check Server
    if not check_server(f"http://{COMFY_HOST}/", COMFY_API_AVAILABLE_MAX_RETRIES, COMFY_API_AVAILABLE_INTERVAL_MS):
        return {"error": "ComfyUI server not reachable"}

    # 3. Upload Images
    # The workflow expects specifically: "zapa1.png", "zapa2.png", "zapa3.png".
    # We need to map the user's uploaded images to these names OR update the workflow to use the uploaded names.
    # It is safer to update the workflow to use the names provided by the user, 
    # BUT keeping the workflow static and renaming files on upload is also an option.
    # Let's assume the user provides images with ANY name, and we assign them to the slots 1, 2, 3.
    
    # Slots in workflow:
    # Node 122 -> zapa1.png (Right/Main?)
    # Node 125 -> zapa2.png (Middle?)
    # Node 126 -> zapa3.png (Left?)
    # The stitching logic (Node 128, 129) suggests an order.
    # We will simply take the first 3 images from input. If fewer than 3, we might reuse or error.
    # For robust FFGO, we really need 3 views usually.
    # If user provides fewer, we'll just cycle them.
    
    image_names = []
    
    # Upload user images 'as is'
    upload_res = upload_images(images_input)
    if upload_res.get("status") == "error":
        return {"error": "Failed to upload images", "details": upload_res}
        
    image_names = [img["name"] for img in images_input]
    
    # 4. Prepare Workflow
    workflow = json.loads(json.dumps(WORKFLOW_TEMPLATE)) # Deep copy
    
    # Inject Prompt
    workflow["6"]["inputs"]["text"] = prompt_text
    
    # Inject Negative Prompt
    workflow["7"]["inputs"]["text"] = negative_text
    
    # Inject Seed
    workflow["57"]["inputs"]["noise_seed"] = seed
    
    # Inject Images (Map inputs to nodes)
    # Ensure we have at least 1 image name
    # Logic: 
    # Node 122 (LoadImage)
    # Node 125 (LoadImage)
    # Node 126 (LoadImage)
    
    # Fallback logic if fewer than 3 images
    img1 = image_names[0]
    img2 = image_names[1] if len(image_names) > 1 else img1
    img3 = image_names[2] if len(image_names) > 2 else img2
    
    workflow["122"]["inputs"]["image"] = img1
    workflow["125"]["inputs"]["image"] = img2
    workflow["126"]["inputs"]["image"] = img3
    
    # 5. Execute
    ws = None
    client_id = str(uuid.uuid4())
    output_files = []
    
    try:
        ws_url = f"ws://{COMFY_HOST}/ws?clientId={client_id}"
        ws = websocket.WebSocket()
        ws.connect(ws_url, timeout=10)
        
        queue_resp = queue_workflow(workflow, client_id)
        prompt_id = queue_resp["prompt_id"]
        print(f"worker-ffgo - Queued prompt_id: {prompt_id}")
        
        # Monitor
        while True:
            out = ws.recv()
            if isinstance(out, str):
                msg = json.loads(out)
                if msg["type"] == "executing":
                    data = msg["data"]
                    if data["node"] is None and data["prompt_id"] == prompt_id:
                        print("worker-ffgo - Execution finished")
                        break
                elif msg["type"] == "execution_error":
                     data = msg["data"]
                     if data["prompt_id"] == prompt_id:
                         raise ValueError(f"Execution error: {data.get('exception_message')}")

        # Retrieve outputs
        history = get_history(prompt_id)
        outputs = history[prompt_id]["outputs"]
        
        results = []
        for node_id, node_out in outputs.items():
            if "videos" in node_out:
                for vid in node_out["videos"]:
                    fname = vid["filename"]
                    data = get_image_data(fname, vid.get("subfolder",""), vid.get("type"))
                    
                    # Upload to RunPod S3 if credential exists
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
        print(f"worker-ffgo - Error: {e}")
        traceback.print_exc()
        return {"error": str(e)}
    finally:
        if ws:
            ws.close()

runpod.serverless.start({"handler": handler})
