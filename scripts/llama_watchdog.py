"""Watchdog for the local llama-server (Gemma 4 E4B).

Monitors server health every N seconds. If the server is down or stalls
(no response within timeout), restarts it with the same config.

Usage:
    python3 scripts/llama_watchdog.py          # runs forever, checks every 30s
    python3 scripts/llama_watchdog.py --once   # single health check
"""

import os, sys, time, subprocess, json, urllib.request

LLAMA_HOST = os.environ.get("LLAMA_HOST", "http://127.0.0.1:8082")
MODEL_PATH = os.path.expanduser("~/models/gemma-4-E4B/gemma-4-E4B-it-ultra-uncensored-heretic-Q4_K_M.gguf")
MMPROJ_PATH = os.path.expanduser("~/models/gemma-4-E4B/gemma-4-E4B-it-mmproj-BF16.gguf")
CTX_SIZE = os.environ.get("LLAMA_CTX", "32768")
PARALLEL = os.environ.get("LLAMA_PARALLEL", "2")
LOG_FILE = "/tmp/llama-watchdog.log"


def log(msg):
    line = f"{time.strftime('%Y-%m-%d %H:%M:%S')} {msg}"
    print(line, flush=True)
    with open(LOG_FILE, "a") as f:
        f.write(line + "\n")


def is_healthy(timeout=8):
    """Return True if the server responds to /health within timeout."""
    try:
        with urllib.request.urlopen(f"{LLAMA_HOST}/health", timeout=timeout) as r:
            body = r.read().decode("utf-8", "replace")
            return "ok" in body
    except Exception:
        return False


def restart_server():
    """Kill any running llama-server and start a fresh one."""
    log("Restarting llama-server...")
    try:
        subprocess.run(["pkill", "-f", "llama-server"], capture_output=True, timeout=10)
    except Exception:
        pass
    time.sleep(3)

    # Determine host/port from LLAMA_HOST
    host = LLAMA_HOST.replace("http://", "").split(":")[0]
    try:
        port = LLAMA_HOST.split(":")[2]
    except IndexError:
        port = "8082"

    cmd = [
        "/usr/local/bin/llama-server",
        "--model", MODEL_PATH,
        "--mmproj", MMPROJ_PATH,
        "--host", host,
        "--port", port,
        "--ctx-size", CTX_SIZE,
        "--parallel", PARALLEL,
        "-ngl", "99",
        "-fa", "on",
    ]
    log(f"Launching: {' '.join(cmd)}")
    with open("/tmp/llama-server.log", "w") as out:
        subprocess.Popen(cmd, stdout=out, stderr=subprocess.STDOUT)
    return True


def main():
    check_interval = int(os.environ.get("LLAMA_WATCH_INTERVAL", "30"))
    down_count = 0

    if "--once" in sys.argv:
        ok = is_healthy()
        print("HEALTHY" if ok else "DOWN")
        return 0 if ok else 1

    log(f"Watchdog started. Checking {LLAMA_HOST} every {check_interval}s.")

    while True:
        time.sleep(check_interval)
        if is_healthy():
            if down_count > 0:
                log("Server recovered.")
            down_count = 0
            continue
        down_count += 1
        log(f"Health check failed (attempt {down_count}).")
        if down_count >= 2:  # Two consecutive failures → restart
            restart_server()
            down_count = 0


if __name__ == "__main__":
    sys.exit(main())
