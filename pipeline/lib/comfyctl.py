#!/usr/bin/env python3
"""
comfyctl.py — ComfyUI lifecycle manager for the CBSE pipeline.

Ensures clean VRAM between pipeline stages on RTX 3060 12GB.
"""
import os, signal, subprocess, time, urllib.request, urllib.error

COMFY_DIR = "/home/fiipadmin/comfy/ComfyUI"
COMFY_URL = "http://127.0.0.1:8188"
SUPERVISOR = os.path.join(os.path.dirname(__file__), "run_comfy.sh")


def is_running():
    try:
        with urllib.request.urlopen(f"{COMFY_URL}/system_stats", timeout=3) as r:
            return r.status == 200
    except Exception:
        return False


def pid():
    import re
    try:
        r = subprocess.run(["pgrep", "-f", "main.py.*--listen.*8188"],
                           capture_output=True, text=True, timeout=5)
        if r.returncode == 0 and r.stdout.strip():
            return int(r.stdout.strip().split()[0])
    except Exception:
        pass
    return None


def stop():
    p = pid()
    if not p:
        return True
    try:
        os.kill(p, signal.SIGTERM)
        for _ in range(15):
            time.sleep(1)
            if not pid():
                return True
        os.kill(p, signal.SIGKILL)
        time.sleep(2)
        return pid() is None
    except Exception:
        return False


def start():
    if is_running():
        return True
    try:
        subprocess.Popen(
            ["bash", SUPERVISOR],
            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL
        )
        for _ in range(30):
            time.sleep(2)
            if is_running():
                return True
        return False
    except Exception:
        return False


def restart():
    stop()
    time.sleep(3)
    return start()


def clear_vram():
    """Send a free-memory signal to ComfyUI by interrupting any running job."""
    try:
        req = urllib.request.Request(f"{COMFY_URL}/interrupt", method="POST")
        urllib.request.urlopen(req, timeout=5)
        time.sleep(2)
    except Exception:
        pass
