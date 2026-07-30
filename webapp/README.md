# NexGen — CBSE Video Studio (web app)

A web app to track NCERT explainer-short video production: PDF catalogue with
per-PDF video/version stats, a script editor with PDF-derived draft generation,
reviewable AI image generations with feedback, a HyperFrames template explorer,
and full versioning + feedback history per video.

NexGen is the **tracking/control layer** for the video pipeline. The actual
render pipeline (`build_short.py`, `gen_images.py`, `finalize.py`, the HyperFrames
templates) lives in the parent `cbse-youtube-channel` project and is invoked by
absolute path — this repo does **not** contain the 439 NCERT PDFs or rendered
video binaries.

## Run

```bash
pip install -r requirements.txt
cd webapp/backend
python3 main.py            # serves on http://0.0.0.0:8787
```

Open `http://localhost:8787/` (or the Cloudflare hostname `edu.fixitinpost.in`).

The server auto-scans `PROJECT_ROOT/assets/pdfs` on boot and seeds the three
HyperFrames templates (`science`, `physics`, `maths`) from
`PROJECT_ROOT/pipeline/templates/*.json`. Set `PROJECT_ROOT` if it differs from
the repo's parent-of-parent.

## Architecture

- **Backend:** FastAPI + SQLite (`db.py`). `catalog.py` scans the NCERT PDFs.
- **Frontend:** single static `index.html` SPA (no build step).
- **Database:** `studio.db` (git-ignored). Rebuilt from the PDF scan on boot.

## Hermes compatibility

NexGen is designed to be driven by an AI agent (Hermes). Feedback captured in the
web app feeds Hermes, which creates reworked script drafts and publishes new
versions back. See **[HERMES.md](HERMES.md)** for the integration contract.

## Features

- PDF catalogue with class/subject facets and per-PDF video + version counts.
- Script editor: generate a draft from a PDF, edit segments, save versions.
- Image review: generate segment images, approve/reject with feedback.
- Template explorer: preview Cobalt Grid / Quantum Slate / Euclid Rose palettes.
- Versioning: every saved script is a numbered version; feedback is per version.

## License

MIT — open source, public.
