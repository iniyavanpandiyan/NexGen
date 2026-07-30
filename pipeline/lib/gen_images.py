#!/usr/bin/env python3
"""
gen_images.py -- generate one illustration per script segment
via the local ComfyUI Z-Image-Turbo endpoint with in-Workflow color correction.

Reads:  pipeline/rendered/<slug>/script.json
Writes: pipeline/rendered/<slug>/images/seg_NN.png
        pipeline/rendered/<slug>/images/manifest.json

Z-Image-Turbo workflow + ColorCorrect:
  UNETLoader -> ModelSamplingAuraFlow
  CLIPLoader(qwen) -> CLIPTextEncode
  ConditioningZeroOut (neg)
  EmptySD3LatentImage -> KSampler -> VAEDecode -> ColorCorrect -> SaveImage

Resilience: per-segment retry, resume (skip done), and --supervisor.
"""
import argparse, json, os, sys, time, urllib.request, urllib.error, shutil, subprocess

ROOT = "/home/fiipadmin/projects/cbse-youtube-channel"
COMFY = "http://127.0.0.1:8188"
COMFY_OUT = "/home/fiipadmin/comfy/ComfyUI/output"
SUPERVISOR = os.path.join(ROOT, "pipeline", "lib", "run_comfy.sh")

UNET = "z_image_turbo_fp8_e4m3fn.safetensors"
CLIP = "qwen_3_4b_bf16_fp8_scaled.safetensors"
VAE = "ae.safetensors"

STYLE = (" educational infographic, cream #F4F1EA background, "
         "cobalt blue #1A3FB0 line-art and fills, flat vector style, "
         "clean composition, soft warm lighting, high contrast")
NEG = "text, letters, words, numbers, photo, realistic, 3d render, shadow, gradient, blurry, low quality"


def comfy_up():
    try:
        with urllib.request.urlopen(f"{COMFY}/system_stats", timeout=5) as r:
            return r.status == 200
    except Exception:
        return False


def ensure_comfy():
    if comfy_up():
        return True
    try:
        subprocess.Popen(["bash", SUPERVISOR],
                         stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    except Exception:
        return False
    for _ in range(24):
        time.sleep(5)
        if comfy_up():
            return True
    return False


def post(payload):
    data = json.dumps(payload).encode()
    req = urllib.request.Request(COMFY + "/prompt", data=data,
                                 headers={"Content-Type": "application/json"}, method="POST")
    with urllib.request.urlopen(req, timeout=60) as r:
        return json.loads(r.read().decode())


def history(pid, timeout=240):
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
    raise TimeoutError("timeout waiting for result")


def get_file_of_prefix(prefix):
    try:
        with urllib.request.urlopen(f"{COMFY}/view?filename=&type=output&subfolder=", timeout=10) as r:
            pass
    except Exception:
        pass
    import glob
    cands = sorted(glob.glob(os.path.join(COMFY_OUT, prefix + "*")),
                   key=os.path.getmtime, reverse=True)
    return cands[0] if cands else None


def zimage_graph(prompt, seed, size=1024):
    return {
        "28": {"class_type": "UNETLoader", "inputs": {"unet_name": UNET, "weight_dtype": "default"}},
        "30": {"class_type": "CLIPLoader", "inputs": {"clip_name": CLIP, "type": "lumina2", "device": "default"}},
        "27": {"class_type": "CLIPTextEncode", "inputs": {"clip": ["30", 0], "text": prompt + STYLE}},
        "33": {"class_type": "ConditioningZeroOut", "inputs": {"conditioning": ["27", 0]}},
        "13": {"class_type": "EmptySD3LatentImage", "inputs": {"width": size, "height": size, "batch_size": 1}},
        "11": {"class_type": "ModelSamplingAuraFlow", "inputs": {"model": ["28", 0], "shift": 3.0}},
        "3": {"class_type": "KSampler", "inputs": {
            "model": ["11", 0], "positive": ["27", 0], "negative": ["33", 0],
            "latent_image": ["13", 0], "seed": seed, "steps": 8, "cfg": 1.0,
            "sampler_name": "res_multistep", "scheduler": "simple", "denoise": 1.0}},
        "8": {"class_type": "VAEDecode", "inputs": {"samples": ["3", 0], "vae": ["29", 0]}},
        "29": {"class_type": "VAELoader", "inputs": {"vae_name": VAE}},
        "31": {"class_type": "ColorCorrect", "inputs": {
            "image": ["8", 0], "temperature": 0, "hue": 0,
            "brightness": 0, "contrast": 0, "saturation": 0, "gamma": 1.0}},
        "9": {"class_type": "SaveImage", "inputs": {"images": ["31", 0], "filename_prefix": "cbse"}},
    }


def gen_one(slug_dir, idx, prompt, seed, retries):
    img_dir = os.path.join(slug_dir, "images")
    prefix = f"cbse_{slug_dir.split('/')[-1]}_{idx:02d}"
    last_err = None
    for attempt in range(1, retries + 1):
        if not comfy_up() and not ensure_comfy():
            last_err = "comfy unavailable"; time.sleep(6); continue
        try:
            g = zimage_graph(prompt, seed)
            g["9"]["inputs"]["filename_prefix"] = prefix
            pid = post({"prompt": g, "client_id": f"cbse-{slug_dir}"})["prompt_id"]
            h = history(pid)
            if "outputs" not in h:
                last_err = "no outputs"; time.sleep(5); continue
            fname = h["outputs"]["9"]["images"][0]["filename"]
            src = os.path.join(COMFY_OUT, fname)
            if not os.path.exists(src):
                src = get_file_of_prefix(prefix) or get_file_of_prefix("cbse")
            if not src or not os.path.exists(src):
                last_err = "missing file"; time.sleep(5); continue
            dst = os.path.join(img_dir, f"seg_{idx:02d}.png")
            shutil.copy(src, dst)
            return True, None
        except Exception as e:
            last_err = f"{type(e).__name__}: {e}"
            if not comfy_up():
                ensure_comfy()
            time.sleep(5)
    return False, last_err


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--slug", required=True)
    ap.add_argument("--steps", type=int, default=8)
    ap.add_argument("--seed-base", type=int, default=1234)
    ap.add_argument("--retries", type=int, default=4)
    ap.add_argument("--supervisor", action="store_true")
    args = ap.parse_args()

    slug_dir = os.path.join(ROOT, "pipeline", "rendered", args.slug)

    if args.supervisor:
        ensure_comfy()
    script = json.load(open(os.path.join(slug_dir, "script.json")))
    segs = script["segments"]
    img_dir = os.path.join(slug_dir, "images")
    os.makedirs(img_dir, exist_ok=True)
    manifest = {}
    mp = os.path.join(img_dir, "manifest.json")
    if os.path.exists(mp):
        try:
            manifest = {int(k): v for k, v in json.load(open(mp)).items()}
        except Exception:
            manifest = {}

    done = 0
    for s in segs:
        idx = s["index"]
        rel = f"images/seg_{idx:02d}.png"
        done_path = os.path.join(img_dir, f"seg_{idx:02d}.png")
        if os.path.exists(done_path) and os.path.getsize(done_path) > 2000:
            manifest.setdefault(idx, {"file": rel})
            print(f"[skip] seg {idx} exists", file=sys.stderr); done += 1; continue
        prompt = s.get("image_prompt", s["text"])
        ok, err = gen_one(slug_dir, idx, prompt, args.seed_base + idx * 7, args.retries)
        if ok:
            manifest[idx] = {"file": rel, "prompt": prompt, "seed": args.seed_base + idx * 7}
            print(f"[ok] seg {idx}", file=sys.stderr); done += 1
        else:
            print(f"[FAIL] seg {idx}: {err}", file=sys.stderr)
    json.dump(manifest, open(mp, "w"), indent=2)
    print(json.dumps({"slug": args.slug, "images": len(manifest), "done": done}))


if __name__ == "__main__":
    sys.exit(main())
