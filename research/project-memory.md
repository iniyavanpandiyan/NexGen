# CBSE YouTube Channel - Project Memory

## Overview
Building a YouTube channel using **Remotion** to produce short educational videos (~60s vertical shorts) from CBSE/NCERT textbooks (Classes 9-12, all streams). Daily upload target with AI TTS narration.

## Current Status - July 2026

### Phase 1: COMPLETED ✅
- Source verified: NCERT24.com (fully accessible, no auth required)
- Comprehensive catalogue created with all subjects/classes
- Quick reference guide created

### Known Gaps (unresolved before migration)
- **Class 11 Mathematics** missing from catalogue entirely — needs to be found on NCERT24
- **Class 10 Social Science chapter-wise** — has book PDF but need to check for chapter URLs
- Some subjects only have book-level PDFs, not chapter-by-chapter content
- Need to verify all subjects for classes 9-12 are accounted for

### Project Structure
- `research/` — catalogues, plan, quick-reference
- `assets/` — empty, needs media assets
- `scripts/` — empty, needs automation scripts
- `remotion-app/` — empty, needs Remotion project setup

## Key Decisions
1. Format: Vertical Shorts/Reels (60s)
2. Voiceover: AI Text-to-Speech
3. Frequency: Daily uploads
4. Coverage: All streams (Science, Commerce, Arts)
5. Source: NCERT24.com

## Priority Subjects
1. Mathematics (Class 9-12)
2. Physics (Class 11-12)
3. Chemistry (Class 11-12)
4. Science (Class 9-10)
5. Social Science (Class 9-10)

## What To Do Next
Finish Phase 1: find all missing subjects on NCERT24.com, then move to Phase 2 (set up Remotion dev environment + video pipeline).

## Migration History
- **July 3, 2026:** Migrated from OpenClaw workspace to Hermes
- All source data preserved at `/home/fiipadmin/.openclaw/workspace/cbse-youtube-channel/`
- Project now managed at `/home/fiipadmin/projects/cbse-youtube-channel/`
