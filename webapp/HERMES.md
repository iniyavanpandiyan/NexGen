# HERMES.md — Agent (Hermes) Integration Contract

NexGen is **agent-driveable**. A Hermes session (or a scheduled Hermes agent)
can close the production loop autonomously:

```
feedback (web app) → Hermes reads open feedback
                   → Hermes drafts a reworked script (may pull PDF text)
                   → Hermes POSTs the draft as a new version
                   → user confirms / edits in the web app
                   → pipeline renders + finalizes → POSTs the new version's video
                   → it appears in the web app automatically
```

No video-pipeline code lives in this repo. The agent drives NexGen over HTTP and
the pipeline is invoked by the server via absolute path (see `main.py`).

## Base URL

- Local: `http://localhost:8787`
- Public: `https://edu.fixitinpost.in`

All endpoints are under `/api/`. Responses are JSON unless noted.

## Data model (SQLite)

| Table     | Key fields                                                            |
|-----------|-----------------------------------------------------------------------|
| `videos`  | `id, pdf_id, slug, title, subject, class, template_id, status`        |
| `versions`| `id, video_id, version_no, script_json, note, author`                 |
| `feedback`| `id, video_id, version_id, kind, text, author, status (open/resolved)`|
| `images`  | `id, video_id, version_id, seg_index, path, prompt, status, feedback` |

`script_json` shape:
```json
{"title": "...", "subject": "...", "class": "...",
 "template_id": "science|physics|maths",
 "segments": [{"index": 0, "text": "...", "image_prompt": "..."}, ...]}
```

## Loop endpoints

| Step | Method & path | Purpose |
|------|---------------|---------|
| List videos needing work | `GET /api/videos?status=needs_rework` | find targets |
| Read a video + its versions + feedback + images | `GET /api/videos/{id}` | full context |
| Read open feedback for a video | `GET /api/videos/{id}` → filter `feedback[]` where `status=="open"` | what to fix |
| Read a specific version's script | from `versions[].script_json` | current script |
| **Create reworked draft** | `POST /api/videos/{id}/save-version` `{segments, note, author:"hermes"}` | new numbered version |
| Mark video back to draft | (server sets `status='draft'` on save-version) | ready for render |
| Register rendered video | see "Render hook" below | publish result |
| Resolve feedback | `POST /api/videos/{id}/feedback` (add note) then mark resolved via `PATCH` (see note) | close the loop |

## Render hook (publishing a version's video)

The server renders on demand via:
- `POST /api/videos/{id}/generate-images` → `{"task_id"}` (then poll `GET /api/tasks/{task_id}`)
- `POST /api/videos/{id}/build` → `{"task_id"}` (then poll; builds + finalizes → `pipeline/rendered/{slug}/final.mp4`)

The agent can trigger these, or wait for the user to click "Build" in the UI.
Once built, the video is served at `GET /api/videos/{id}/final`.

> **Convention for agent-authored work:** set `author:"hermes"` on
> `save-version` so the UI can show "Hermes rework vN" distinctly from manual edits.

## State machine

```
video.status: draft ──▶ rendering ──▶ ready ──▶ needs_rework ──▶ draft ...
feedback.status: open ──▶ resolved
```

A typical Hermes pass:
1. `GET /api/videos?status=needs_rework` (or scan all videos for `open` feedback).
2. For each, `GET /api/videos/{id}`, read `feedback[]` with `status=="open"`.
3. Modify `versions[last].script_json.segments` per feedback.
4. `POST /api/videos/{id}/save-version` with `author:"hermes"`.
5. (Optional) `POST /api/videos/{id}/build` to render; poll task; confirm `final.mp4`.
6. The new version + video now show in the web app with no manual DB work.

## Notes for the agent

- Always quote the NCERT source PDF id (`pdf_id`) — never invent content outside it.
- Keep segments ≤ ~16 words for clean TTS pacing (the build step aligns audio).
- `template_id` chooses the visual palette: `science` (Cobalt Grid),
  `physics` (Quantum Slate), `maths` (Euclid Rose).
- The DB file `studio.db` is local state and is git-ignored; it is rebuilt by
  `catalog.scan` on boot. Do not rely on it persisting across a fresh clone.
