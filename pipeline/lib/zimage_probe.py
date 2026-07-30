#!/usr/bin/env python3
"""Probe the Z-Image-Turbo ComfyUI workflow with one simple prompt."""
import json, urllib.request, time, sys, os

COMFY = "http://127.0.0.1:8188"
COMFY_OUT = "/home/fiipadmin/comfy/ComfyUI/output"


def post(payload):
    data = json.dumps(payload).encode()
    req = urllib.request.Request(COMFY + "/prompt", data=data,
                                 headers={"Content-Type": "application/json"}, method="POST")
    with urllib.request.urlopen(req, timeout=60) as r:
        return json.loads(r.read().decode())


def history(pid, timeout=180):
    dl = time.time() + timeout
    while time.time() < dl:
        try:
            with urllib.request.urlopen(f"{COMFY}/history/{pid}", timeout=10) as r:
                d = json.loads(r.read().decode())
        except Exception:
            time.sleep(2); continue
        if pid in d:
            return d[pid]
        time.sleep(2)
    raise TimeoutError("timeout")


def graph(prompt, seed):
    return {
        "1": {"class_type": "UNETLoader", "inputs": {"unet_name": "z_image_turbo_fp8_e4m3fn.safetensors", "weight_dtype": "default"}},
        "2": {"class_type": "CLIPLoader", "inputs": {"clip_name": "qwen_3_4b_bf16_fp8_scaled.safetensors", "type": "qwen_image"}},
        "3": {"class_type": "TextEncodeZImageOmni", "inputs": {"clip": ["2", 0], "prompt": prompt, "auto_resize_images": True}},
        "4": {"class_type": "VAELoader", "inputs": {"vae_name": "ae.safetensors"}},
        "5": {"class_type": "EmptyLatentImage", "inputs": {"width": 1024, "height": 1024, "batch_size": 1}},
        "6": {"class_type": "SDTurboScheduler", "inputs": {"model": ["1", 0], "steps": 1, "denoise": 1.0}},
        "7": {"class_type": "KSampler", "inputs": {"model": ["6", 0], "positive": ["3", 0], "negative": ["3", 0], "seed": seed, "steps": 1, "cfg": 1.0, "sampler_name": "euler", "scheduler": "simple", "denoise": 1.0, "latent_image": ["5", 0]}},
        "8": {"class_type": "VAEDecode", "inputs": {"samples": ["7", 0], "vae": ["4", 0]}},
        "9": {"class_type": "SaveImage", "inputs": {"images": ["8", 0], "filename_prefix": "zprobe"}},
    }


def main():
    prompt = sys.argv[1] if len(sys.argv) > 1 else "a single red apple on a white background"
    seed = int(sys.argv[2]) if len(sys.argv) > 2 else 1234
    resp = post({"prompt": graph(prompt, seed), "client_id": "zprobe"})
    print("prompt_id:", resp.get("prompt_id"))
    h = history(resp["prompt_id"])
    if "outputs" not in h:
        print("NO OUTPUTS:", json.dumps(h)[:2000]); return
    im = h["outputs"]["9"]["images"][0]
    print("image:", im)
    src = os.path.join(COMFY_OUT, im["filename"])
    dst = "/tmp/zprobe_out.png"
    if os.path.exists(src):
        import shutil; shutil.copy(src, dst); print("saved", dst)
    # error surfacing
    if "status" in h and h["status"].get("status_str") != "success":
        print("STATUS:", json.dumps(h["status"])[:1500])


if __name__ == "__main__":
    main()
