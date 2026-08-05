# NexGen — Product Vision (Source of Truth)

This document is the canonical description of what NexGen is and how it works.
Every feature decision must stay aligned with this vision. If a change drifts
from this document, update THIS file first — then change the code to match.

Last revised: 2026-08-05

---

## What NexGen is

A content factory that turns NCERT / CBSE textbooks (Classes 9–12, all
streams) into short-form educational videos for social media (YouTube Shorts,
Instagram Reels). A human reviews and approves every deliverable. Nothing goes
out automatically without approval.

---

## The Pipeline — 5 Steps

### 1. Identify (OCR)

The first step takes a PDF and produces a clean, LLM-ready text layer.

- **OCR is the default.** The PDF's own text layer (pypdf) is used only as a
  fallback when OCR is unavailable; we do not prefer pypdf, its formatting is
  not good enough.
- OCR runs through the **Unlimited-OCR model** hosted on the GPU orchestrator
  (single RTX 3060, 12GB, shared with Gemma 4 E4B + Whisper). The orchestrator
  swaps the active GPU model on demand.
- Output: a cleaned, de-garbled, reading-order text blob that the rest of the
  pipeline can rely on.

**Outcome:** every chapter / PDF has a trustworthy text layer, ready for
context generation.

### 2. Context Generation

Turns the raw OCR text into structured "context parts" — isolated segments of
the chapter, each self-contained and dense enough to feed script generation.

- Runs on **Gemma 4 E4B** (local) or **OpenRouter** — whichever is configured
  (Settings tab / `llm_backend`).
- The model walks the entire chapter (or whole PDF) and emits raw context
  **part by part, sequentially**, until the full document is covered.
- Each part is one self-contained unit of understanding: it carries the
  meaning, entities, facts and flow of that slice of the chapter.

**Outcome:** `N` context parts. `N` is flexible — the model decides the natural
split, and the parts are later repurposed across different video lengths.

### 3. Generate Scripts

This is where the **variant** is chosen and where each context part becomes an
engaging script.

- A **variant** is a script "format + treatment":
  - **Song Variant** — lyric/structure-based adaptation.
  - **Conversational Variant** — dialogue/two-host format.
  - **Image Slideshow Variant** — narration built around a sequence of stills.
  - ...and more can be added. Each variant is custom-catered to work with
    HyperFrames compositions.
- Generation runs **per context part**: because each part is isolated, the
  script model can focus on the nuances of the variant and how the raw context
  adapts to it (hooks, cliffhangers, retention elements).
- Scripts are **proofread, verified and approved by a human** before they enter
  the queue.
- **Each script is a separate queue item.** One chapter at ~1 min/script with 12
  context parts → 12 queue items. At ~3 min/script → 4 scripts. The segmented
  parts are repurposed efficiently across lengths.
- At this step the user also sets:
  - **Time limit / duration** per script.
  - **Aspect ratio** (horizontal / vertical / square).

**Outcome:** approved, variant-tagged script items, each with its own context,
variant, and metadata, ready for the queue.

### 4. Queue → Agent Build

The agent takes over.

- For each queue item, the agent creates a **coherent HyperFrames project** for
  that script. Each script brings its own context, variant and metadata, so the
  composition matches the intended treatment.
- When the agent finishes a video, it **updates the queue item** and flags it
  **for review**.
- This feeds a **feedback loop**: the video only reaches "done" when the user
  approves the generated video / composition. Rejections cycle back.

**Outcome:** a reviewed, approved HyperFrames video per queue item.

### 5. Upload

The approved video is finalized for publishing.

- All required metadata is prepared (title, description, tags, etc.).
- **Thumbnail generation** optimized for social media upload.
- **Horizontal and vertical** variants depending on user preference.
- Files are auto-uploaded to **YouTube and Instagram** (preferred), on approval.

**Outcome:** published content, end-to-end.

---

## Cross-cutting Rules

1. **OCR is the default text source** (step 1). pypdf is a fallback only.
2. **Context parts are the atomic unit** (step 2) — scripts, durations and
   variant adaptations are all built from them.
3. **Variants are first-class.** They are chosen at script generation time
   (step 3) and are carried through the queue (step 4) as metadata.
4. **Human-in-the-loop.** Approve scripts before queueing; approve videos
   before upload. No unapproved auto-publish.
5. **One script = one queue item = one HyperFrames project.**
6. **UI should be professional and clean** — claymorphic design (or similar),
   minimal, polished. Not cluttered.

---

## Current Implementation Status

- **Step 1 (OCR):** DONE — Unlimited-OCR via GPU orchestrator is wired into
  `pipeline/lib/text_extractor.py` as the default path, with pypdf fallback.
  Hallucinated OCR filler is filtered.
- **Step 2 (Context generation):** IN PROGRESS — chunked generation exists in
  `pipeline/lib/llm_script_gen.py` but must become the sequential, isolated
  context-part model described above.
- **Step 3 (Variant scripts):** NOT BUILT — no variant system yet. Parse is
  one-shot script generation; needs context-part-driven, variant-aware
  generation + duration + aspect-ratio controls.
- **Step 4 (Queue → agent → HyperFrames):** PARTIAL — render queue + HyperFrames
  render trigger exist; per-script HyperFrames project building and the
  review/feedback loop need to align with the variant metadata model.
- **Step 5 (Upload):** NOT BUILT — no YouTube/Instagram upload path yet.
- **UI:** NOT DONE — current single-file `index.html` needs the claymorphic /
  professional redesign and the new step-centric flow.
