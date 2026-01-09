import requests
import base64
import json
import time
import os
import sys
import random

# -------------------------------------------------------------------------
# CONFIGURATION
# -------------------------------------------------------------------------
RUNPOD_API_KEY = os.environ.get("RUNPOD_API_KEY", "YOUR_API_KEY_HERE")
ENDPOINT_ID = os.environ.get("RUNPOD_ENDPOINT_ID", "jhh23hrm2kr64w")
API_URL = f"https://api.runpod.ai/v2/{ENDPOINT_ID}/run"
STATUS_URL_TEMPLATE = f"https://api.runpod.ai/v2/{ENDPOINT_ID}/status/{{}}"

# URLs (3 Required)
IMAGE_URLS = [
    "https://nikeuyprod.vtexassets.com/arquivos/ids/386505-1200-1200?width=1200&height=1200&aspect=true",
    "https://nikeuyprod.vtexassets.com/arquivos/ids/392714-1200-1200?width=1200&height=1200&aspect=true", 
    "https://nikeuyprod.vtexassets.com/arquivos/ids/393586-1200-1200?width=1200&height=1200&aspect=true"
]

POSITIVE_PROMPT = "High-end ecommerce product video of Nike sneakers..."
NEGATIVE_PROMPT = "low quality, blurry"
SEED = random.randint(1, 9999999999)

# Local path to template (must assume it exists relative to script)
# Path relative to this script
BASE_DIR = os.path.dirname(os.path.abspath(__file__))
WORKFLOW_TEMPLATE_PATH = os.path.join(BASE_DIR, "src", "workflow_ffgo.json")

# -------------------------------------------------------------------------

def download_and_encode_image(url):
    try:
        print(f"Downloading {url}...")
        response = requests.get(url, timeout=30)
        response.raise_for_status()
        return base64.b64encode(response.content).decode("utf-8")
    except Exception as e:
        print(f"Error downloading {url}: {e}")
        return None

def main():
    if not os.path.exists(WORKFLOW_TEMPLATE_PATH):
        print(f"Error: Workflow template not found at {WORKFLOW_TEMPLATE_PATH}")
        return

    # 1. Load Template
    with open(WORKFLOW_TEMPLATE_PATH, "r", encoding="utf-8") as f:
        workflow = json.load(f)

    # 2. Download Images
    encoded_images = []
    image_names = []
    print("Downloading images...")
    for idx, url in enumerate(IMAGE_URLS):
        b64 = download_and_encode_image(url)
        if not b64: return
        name = f"input_{idx}.png"
        encoded_images.append({"name": name, "image": b64})
        image_names.append(name)

    # 3. Modify Workflow (Inject values into specific nodes)
    print("Injecting values into workflow...")
    
    # Prompt (Node 6)
    workflow["6"]["inputs"]["text"] = POSITIVE_PROMPT
    
    # Negative (Node 7)
    workflow["7"]["inputs"]["text"] = NEGATIVE_PROMPT
    
    # Seed (Node 57)
    workflow["57"]["inputs"]["noise_seed"] = SEED
    
    # Images (Nodes 122, 125, 126)
    # Using the names we just assigned
    if len(image_names) >= 3:
        workflow["122"]["inputs"]["image"] = image_names[0]
        workflow["125"]["inputs"]["image"] = image_names[1]
        workflow["126"]["inputs"]["image"] = image_names[2]
    else:
        print("Warning: Fewer than 3 images provided.")
        return

    # 4. Build Payload
    payload = {
        "input": {
            "workflow": workflow,
            "images": encoded_images
        }
    }

    # 5. Send Request
    if ENDPOINT_ID == "YOUR_ENDPOINT_ID_HERE":
        print("Set ENDPOINT_ID to run. Payload prepared.")
        return

    print(f"Sending request to {API_URL}...")
    headers = {"Authorization": f"Bearer {RUNPOD_API_KEY}"}
    
    try:
        resp = requests.post(API_URL, json=payload, headers=headers, timeout=60)
        resp.raise_for_status()
        job = resp.json()
    except Exception as e:
        print(f"Request failed: {e}")
        if 'resp' in locals():
            print(f"Server Response: {resp.text}")
        return
        
    print(f"Job ID: {job['id']}")
    
    # 6. Poll
    while True:
        r = requests.get(STATUS_URL_TEMPLATE.format(job['id']), headers=headers)
        status_data = r.json()
        status = status_data["status"]
        print(f"Status: {status}")
        
        if status == "COMPLETED":
            for item in status_data["output"]:
                fname = item.get("filename", "video.mp4")
                if item["type"] == "base64":
                     with open(fname, "wb") as f:
                         f.write(base64.b64decode(item["data"]))
                elif item["type"] == "s3_url":
                     print(f"Downloading {item['data']}")
                     # download logic here if needed
            print("Done")
            break
        elif status == "FAILED":
            print(status_data)
            break
        time.sleep(5)

if __name__ == "__main__":
    main()
