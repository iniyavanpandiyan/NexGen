"""
VRAM Manager for NexGen.
Intelligently manages GPU memory between:
  - Chatterbox TTS Server (:8210) — ~4GB VRAM
  - ComfyUI Z Image Turbo (:8188) — ~7GB VRAM
Total: 12GB RTX 3060 — both cannot run simultaneously.

Strategy: kill idle service before launching the other.
Services auto-restart on demand (lazy loading).
"""
import os, time, signal, subprocess, json, urllib.request, urllib.error
from pathlib import Path

# Service definitions
SERVICES = {
    "chatterbox": {
        "pid_file": Path("/tmp/.cbse_chatterbox.pid"),
        "port": 8004,
        "health_url": "http://127.0.0.1:8004/",
        "start_cmd": [
            "python3", "/home/fiipadmin/Chatterbox-TTS-Server/server.py"
        ],
        "workdir": "/home/fiipadmin/Chatterbox-TTS-Server",
        "vram_mb": 4200,
    },
    "comfy": {
        "pid_file": Path("/tmp/.cbse_comfy.pid"),
        "port": 8188,
        "health_url": "http://127.0.0.1:8188/system_stats",
        "start_cmd": [
            "/home/fiipadmin/comfy/ComfyUI/.venv/bin/python",
            "main.py", "--listen", "127.0.0.1", "--port", "8188",
            "--lowvram", "--highvram"
        ],
        "workdir": "/home/fiipadmin/comfy/ComfyUI",
        "vram_mb": 7200,
    }
}

# Total VRAM budget (leave 1GB headroom)
VRAM_BUDGET = 11000  # 11GB of 12GB


def _read_pid(name):
    """Read PID from pidfile, or None."""
    pf = SERVICES[name]["pid_file"]
    if pf.exists():
        try:
            return int(pf.read_text().strip())
        except (ValueError, OSError):
            pass
    return None


def _write_pid(name, pid):
    SERVICES[name]["pid_file"].write_text(str(pid))


def _remove_pid(name):
    pf = SERVICES[name]["pid_file"]
    if pf.exists():
        pf.unlink()


def is_alive(pid, port):
    """Check if a process is running and listening on its port."""
    if pid is None:
        return False
    try:
        os.kill(pid, 0)  # check existence
    except (OSError, PermissionError):
        return False
    # Check port responsiveness
    try:
        svc = SERVICES[port] if isinstance(port, str) else None
        url = svc["health_url"] if svc else f"http://127.0.0.1:{port}/"
        with urllib.request.urlopen(url, timeout=3) as r:
            return r.status < 500
    except Exception:
        return False


def kill(name_or_pid, wait=5):
    """
    Kill a service by name or PID. Attempts SIGTERM first, then SIGKILL.
    Also discovers processes by port as a fallback.
    Returns True if the process was killed, False if already gone.
    """
    pid = _read_pid(name_or_pid) if isinstance(name_or_pid, str) else name_or_pid
    if pid is None and isinstance(name_or_pid, str):
        # Fallback: discover by port
        cfg = SERVICES.get(name_or_pid)
        if cfg:
            try:
                result = subprocess.run(
                    ["ss", "-tlnp", f"sport = :{cfg['port']}"],
                    capture_output=True, text=True, timeout=5
                )
                for line in result.stdout.splitlines():
                    if "pid=" in line:
                        pid = int(line.split("pid=")[1].split(",")[0])
                        break
            except Exception:
                pass
    pid = pid or (name_or_pid if isinstance(name_or_pid, int) else None)
    if pid is None:
        return False
    try:
        os.kill(pid, 0)  # existence check
    except OSError:
        _remove_pid(name_or_pid) if isinstance(name_or_pid, str) else None
        return False

    # SIGTERM
    try:
        os.kill(pid, signal.SIGTERM)
    except OSError:
        pass

    # Wait then SIGKILL
    deadline = time.time() + wait
    while time.time() < deadline:
        try:
            os.kill(pid, 0)
        except OSError:
            _remove_pid(name_or_pid) if isinstance(name_or_pid, str) else None
            return True
        time.sleep(0.3)
    try:
        os.kill(pid, signal.SIGKILL)
    except OSError:
        pass
    _remove_pid(name_or_pid) if isinstance(name_or_pid, str) else None
    return True


def ensure_running(name):
    """
    Ensure a service is running. Kills conflicting services first.
    Returns True if the service is now running (or was already).
    """
    cfg = SERVICES[name]
    pid = _read_pid(name)
    if is_alive(pid, cfg["port"]):
        return True  # Already running

    # Determine what to kill (everything except ourselves that uses VRAM)
    for other_name in SERVICES:
        if other_name == name:
            continue
        other_pid = _read_pid(other_name)
        if is_alive(other_pid, SERVICES[other_name]["port"]):
            print(f"[VRAM] Killing {other_name} (pid {other_pid}) to free VRAM for {name}")
            kill(other_name)

    # Wait for port to be free (linger)
    deadline = time.time() + 5
    while time.time() < deadline:
        try:
            with urllib.request.urlopen(cfg["health_url"], timeout=2):
                # Port still bound — wait for old process to die
                time.sleep(0.5)
        except Exception:
            break

    # Start the service
    print(f"[VRAM] Starting {name}...")
    proc = subprocess.Popen(
        cfg["start_cmd"],
        cwd=cfg["workdir"],
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        preexec_fn=os.setsid,
    )
    _write_pid(name, proc.pid)

    # Wait for health
    deadline = time.time() + 60
    started = time.time()
    while time.time() < deadline:
        try:
            with urllib.request.urlopen(cfg["health_url"], timeout=5) as r:
                if r.status < 500:
                    elapsed = time.time() - started
                    print(f"[VRAM] {name} ready in {elapsed:.1f}s (pid {proc.pid})")
                    return True
        except Exception:
            pass
        time.sleep(2)

    # Failed to start — clean up
    print(f"[VRAM] FAILED to start {name} after 60s")
    kill(name)
    return False


def release_all(stop_chatterbox=False, stop_comfy=False):
    """Kill specified services to free all VRAM for other tasks."""
    if stop_chatterbox:
        kill("chatterbox")
    if stop_comfy:
        kill("comfy")
    return True


# Lazy import wrappers for the webapp endpoints
def ensure_chatterbox():
    return ensure_running("chatterbox")


def ensure_comfy():
    return ensure_running("comfy")