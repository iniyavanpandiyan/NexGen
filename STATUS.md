# CBSE YouTube Channel - Project Status

## Current Status: Phase 2 COMPLETE (infra ready), Phase 3 PENDING decision on render stack

_Last updated: 2026-07-09_

### ✅ Phase 1: Research & Catalogue — COMPLETED
- Source: NCERT24.com
- Catalogue: Classes 6–12, all subjects
- Known gaps: Class 9/10 Hindi, Class 12 English/Hindi, Sanskrit (no PDF)

### ✅ Phase 2: Assets & Render Infra — COMPLETED (2026-07-09)
- **439 chapter PDFs** migrated to `assets/pdfs/{class-N}/{subject}/` (1.2 GB, verified count matches source).
  - Old location was `~/.openclaw/workspace/cbse-youtube-channel/assets/pdfs/` — now the active project is the single source of truth.
- **Remotion app** renders verified (1080x1920 @ 30fps MP4). Fixed broken entry: `index.tsx` → `Root.tsx` (`<Composition>`) → `HelloWorld.tsx`.
- **HyperFrames installed** (`projects/cbse-youtube-channel/hyperframes/`, cloned `heygen-com/hyperframes`):
  - bun 1.3.14 installed at `~/.bun/bin` (added to PATH via ~/.bashrc).
  - `bun install` (1563 pkgs) + `bun run build` all green.
  - CLI v0.7.46 works: `npx hyperframes --help` shows render/preview/lint/tts/transcribe.
  - `doctor`: ✓ Node 22, FFmpeg 6.1.1, Chrome headless-shell, Docker running. Optional local TTS (Kokoro) / BGM (MusicGen) NOT installed — not needed (use Nous OpenAI TTS).
  - Telemetry disabled.

### ⬜ Phase 3: Content Pipeline — DECISION PENDING
Open question (asked user 2026-07-09): **Remotion vs HyperFrames?** User chose "set up HyperFrames, decide later."
HyperFrames is a strong fit for the stated goal (per-subject themed templates + faceless explainers from chapter text):
- 50+ installable "blocks" (title cards, lower-thirds, charts) → reusable themed templates.
- Agent skills via `npx skills add heygen-com/hyperframes --all --full-depth` (router `/hyperframes`, workflows incl. `/faceless-explainer`, `/remotion-to-hyperframes`).
- Built-in `tts` (Kokoro local, optional) + `transcribe` (whisper, word-level captions).

## Next Actions
1. **Decide render stack** (Remotion vs HyperFrames) — recommended: standardize on HyperFrames for themed faceless explainers.
2. If HyperFrames: `npx skills add heygen-com/hyperframes --all --full-depth`, build one per-subject themed template.
3. Wire narration: prefer Nous OpenAI TTS (active) over local Kokoro; `transcribe` for captions.
4. Produce first real short: pick one chapter → extract key points → script → TTS → themed template → render.

## Key Decisions
1. Format: Vertical Shorts/Reels (1080x1920, 30fps, ~60s)
2. Voiceover: AI TTS (Nous OpenAI TTS)
3. Frequency: Daily uploads
4. Coverage: All streams, Classes 6–12
5. Source: NCERT24.com

## Priority Subjects
1. Mathematics (9–12)  2. Physics (11–12)  3. Chemistry (11–12)
4. Science (9–10)      5. Social Science (9–10)
