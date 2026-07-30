#!/usr/bin/env python3
"""
gen_3d.py -- generate 3D model (GLB) from a script segment image
via the local ComfyUI Trellis2-GGUF endpoint.

Reads:  pipeline/rendered/<slug>/images/seg_NN.png
Writes: pipeline/rendered/<slug>/3d/seg_NN.glb
        pipeline/rendered/<slug>/3d/manifest.json

Workflow:
  Trellis2LoadModel + Trellis2PreProcessImage
  -> Trellis2MeshWithVoxelGenerator (1024_cascade, Q4_K_M)
  -> Trellis2PostProcessAndUnWrapAndRasterizer
  -> Trellis2ExportMesh (GLB)
"""
import argparse, json, os, sys, time, urllib.request, urllib.error, shutil, glob

ROOT = "/home/fiipadmin/projects/cbse-youtube-channel"
COMFY = "http://127.0.0.1:8188"
COMFY_INPUT = "/home/fiipadmin/comfy/ComfyUI/input"
COMFY_OUTPUT = "/home/fiipadmin/comfy/ComfyUI/output"
SUPERVISOR = os.path.join(ROOT, "pipeline", "lib", "run_comfy.sh")


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


def history(pid, timeout=600):
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


def build_workflow(input_filename, prefix, seed):
    return {
        "1": {"class_type": "Trellis2LoadModel", "inputs": {
            "modelname": "TRELLIS.2-4B",
            "model_format": "GGUF Q4_K_M",
            "backend": "xformers",
            "device": "cuda",
            "low_vram": True,
            "keep_models_loaded": True,
        }},
        "2": {"class_type": "Trellis2LoadImageWithTransparency", "inputs": {
            "image": input_filename,
        }},
        "3": {"class_type": "Trellis2PreProcessImage", "inputs": {
            "image": ["2", 2],
            "padding": 0,
            "remove_background": False,
        }},
        "4": {"class_type": "Trellis2MeshWithVoxelGenerator", "inputs": {
            "pipeline": ["1", 0],
            "image": ["3", 0],
            "seed": seed,
            "pipeline_type": "1024_cascade",
            "sparse_structure_steps": 12,
            "shape_steps": 12,
            "texture_steps": 12,
            "max_num_tokens": 49152,
            "sparse_structure_resolution": 32,
            "max_views": 4,
            "generate_texture_slat": True,
            "use_tiled_decoder": True,
            "sampler": "euler",
        }},
        "5": {"class_type": "Trellis2PostProcessAndUnWrapAndRasterizer", "inputs": {
            "mesh": ["4", 0],
            "mesh_cluster_threshold_cone_half_angle_rad": 60.0,
            "mesh_cluster_refine_iterations": 0,
            "mesh_cluster_global_iterations": 1,
            "mesh_cluster_smooth_strength": 1,
            "texture_size": 2048,
            "remesh": True,
            "remesh_band": 1.0,
            "remesh_project": 0.0,
            "target_face_num": 2000000,
            "simplify_method": "Cumesh",
            "fill_holes": True,
            "texture_alpha_mode": "OPAQUE",
            "dual_contouring_resolution": "1024",
            "double_side_material": False,
            "remove_floaters": True,
            "bake_on_vertices": False,
            "use_custom_normals": False,
            "uv_unwrap_method": "Xatlas",
            "bvh": ["4", 1],
            "remove_inner_faces": True,
        }},
        "6": {"class_type": "Trellis2ExportMesh", "inputs": {
            "trimesh": ["5", 0],
            "filename_prefix": prefix,
            "file_format": "glb",
        }},
    }


def gen_one(slug_dir, idx, input_img_path, seed, retries):
    os.makedirs(os.path.join(slug_dir, "3d"), exist_ok=True)
    slug_name = os.path.basename(slug_dir)
    prefix = f"cbse_3d_{slug_name}_{idx:02d}"
    last_err = None

    for attempt in range(1, retries + 1):
        if not comfy_up() and not ensure_comfy():
            last_err = "comfy unavailable"; time.sleep(6); continue
        try:
            input_name = os.path.basename(input_img_path)
            input_dst = os.path.join(COMFY_INPUT, input_name)
            if not os.path.exists(input_dst):
                shutil.copy2(input_img_path, input_dst)

            g = build_workflow(input_name, prefix, seed)
            pid = post({"prompt": g, "client_id": f"cbse-3d-{slug_name}"})["prompt_id"]
            h = history(pid, timeout=600)

            if "outputs" not in h or "6" not in h["outputs"]:
                last_err = "no output from Trellis2ExportMesh"
                time.sleep(5); continue

            glb_rel = h["outputs"]["6"]["glb_path"][0]
            glb_name = os.path.basename(glb_rel)
            src = os.path.join(COMFY_OUTPUT, glb_rel)
            if not os.path.exists(src):
                cands = sorted(glob.glob(os.path.join(COMFY_OUTPUT, prefix + "*")),
                               key=os.path.getmtime, reverse=True)
                src = cands[0] if cands else None
            if not src or not os.path.exists(src):
                last_err = "missing GLB file"; time.sleep(5); continue

            dst = os.path.join(slug_dir, "3d", f"seg_{idx:02d}.glb")
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
    ap.add_argument("--seed-base", type=int, default=5678)
    ap.add_argument("--retries", type=int, default=4)
    ap.add_argument("--supervisor", action="store_true")
    ap.add_argument("--indices", type=str, default=None,
                    help="comma-separated segment indices to generate (default: all)")
    args = ap.parse_args()

    slug_dir = os.path.join(ROOT, "pipeline", "rendered", args.slug)
    if args.supervisor:
        ensure_comfy()

    script = json.load(open(os.path.join(slug_dir, "script.json")))
    segs = script["segments"]

    os.makedirs(os.path.join(slug_dir, "3d"), exist_ok=True)
    manifest = {}
    mp = os.path.join(slug_dir, "3d", "manifest.json")
    if os.path.exists(mp):
        try:
            manifest = json.load(open(mp))
            if isinstance(manifest, dict):
                manifest = {int(k) if k.isdigit() else k: v for k, v in manifest.items()}
        except Exception:
            manifest = {}

    want = None
    if args.indices:
        want = {int(x.strip()) for x in args.indices.split(",") if x.strip().isdigit()}

    done = 0
    for s in segs:
        idx = int(s["index"])
        if want is not None and idx not in want:
            continue

        rel = f"3d/seg_{idx:02d}.glb"
        done_path = os.path.join(slug_dir, rel)
        if os.path.exists(done_path) and os.path.getsize(done_path) > 2000:
            manifest[str(idx)] = {"file": rel}
            print(f"[skip] seg {idx} GLB exists", file=sys.stderr); done += 1; continue

        img_path = os.path.join(slug_dir, "images", f"seg_{idx:02d}.png")
        if not os.path.exists(img_path):
            print(f"[skip] seg {idx}: no input image {img_path}", file=sys.stderr)
            continue

        ok, err = gen_one(slug_dir, idx, img_path, args.seed_base + idx * 7, args.retries)
        if ok:
            manifest[str(idx)] = {"file": rel, "seed": args.seed_base + idx * 7}
            print(f"[ok] 3D seg {idx}", file=sys.stderr)
            done += 1
        else:
            print(f"[FAIL] 3D seg {idx}: {err}", file=sys.stderr)

    json.dump(manifest, open(mp, "w"), indent=2)
    print(json.dumps({"slug": args.slug, "models_3d": len(manifest), "done": done}))


if __name__ == "__main__":
    import subprocess
    sys.exit(main())
