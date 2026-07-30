#!/usr/bin/env python3
"""
Finalize a rendered short: concatenate the per-segment Chatterbox WAVs (in the
exact order/timing the visuals were built from) into one narration track, then
mux it under the silent HyperFrames render. The audio and the visuals are both
driven by the same manifest.json segment timings, so the result is guaranteed
in sync — the picture changes exactly when each spoken segment begins.
"""
import json, os, subprocess, sys, argparse

ROOT = "/home/fiipadmin/projects/cbse-youtube-channel"


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--slug", required=True)
    ap.add_argument("--silent", default=None,
                    help="path to silent.mp4 (default: <slug>/silent.mp4)")
    ap.add_argument("--fps", type=int, default=30)
    args = ap.parse_args()

    d = os.path.join(ROOT, "pipeline", "rendered", args.slug)
    manifest = json.load(open(os.path.join(d, "manifest.json")))
    segs = manifest["segments"]
    silent = args.silent or os.path.join(d, "silent.mp4")
    narr = os.path.join(d, "narration.mp3")
    final = os.path.join(d, "final.mp4")

    if not os.path.exists(silent):
        sys.exit(f"[error] silent video not found: {silent}")

    # 1) Build a single narration track by concatenating per-segment WAVs
    #    in manifest order. Segment audio lives at segments/seg_NN.wav.
    seg_dir = os.path.join(d, "segments")
    concat_list = os.path.join(seg_dir, "concat.txt")
    parts = []
    for s in segs:
        idx = s["index"]
        wav = os.path.join(seg_dir, f"seg_{idx:02d}.wav")
        if not os.path.exists(wav):
            sys.exit(f"[error] missing segment audio: {wav}")
        parts.append(wav)
    with open(concat_list, "w") as f:
        for p in parts:
            f.write(f"file '{p}'\n")

    r = subprocess.run(
        ["ffmpeg", "-y", "-f", "concat", "-safe", "0", "-i", concat_list,
         "-ar", "44100", "-ac", "2", "-b:a", "192k", narr],
        capture_output=True, text=True)
    if r.returncode != 0:
        print("NARR CONCAT FAILED:\n", r.stderr[-800:], file=sys.stderr)
        sys.exit(1)

    # 2) mux narration under the silent video (video stream copied, no re-encode)
    r = subprocess.run(
        ["ffmpeg", "-y", "-i", silent, "-i", narr,
         "-map", "0:v:0", "-map", "1:a:0",
         "-c:v", "copy", "-c:a", "aac", "-shortest",
         "-movflags", "+faststart", final],
        capture_output=True, text=True)
    if r.returncode != 0:
        print("MUX FAILED:\n", r.stderr[-800:], file=sys.stderr)
        sys.exit(1)

    # 3) verify streams
    probe = subprocess.run(
        ["ffprobe", "-v", "error", "-show_entries",
         "stream=codec_type,codec_name,duration", "-of", "json", final],
        capture_output=True, text=True).stdout
    info = json.loads(probe)
    v = [s for s in info["streams"] if s["codec_type"] == "video"]
    a = [s for s in info["streams"] if s["codec_type"] == "audio"]
    vlen = float(v[0]["duration"]) if v and "duration" in v[0] else None
    alen = float(a[0]["duration"]) if a and "duration" in a[0] else None
    print(json.dumps({
        "final": final,
        "has_video": bool(v), "has_audio": bool(a),
        "video_seconds": round(vlen, 3) if vlen else None,
        "audio_seconds": round(alen, 3) if alen else None,
        "manifest_total": manifest["total"],
        "final_size": os.path.getsize(final),
    }, indent=2))


if __name__ == "__main__":
    main()
