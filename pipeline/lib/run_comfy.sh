#!/bin/bash
# ComfyUI supervisor: keep ComfyUI alive across crashes (RTX 3060 VRAM pressure).
cd /home/fiipadmin/comfy/ComfyUI
while true; do
  echo "$(date '+%F %T') [supervisor] starting ComfyUI"
  ./.venv/bin/python main.py --listen 127.0.0.1 --port 8188 --lowvram
  echo "$(date '+%F %T') [supervisor] ComfyUI exited (code $?); restarting in 5s"
  sleep 5
done
