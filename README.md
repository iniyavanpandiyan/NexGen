# CBSE YouTube Channel Project

## Overview
Building a YouTube channel using **Remotion** to produce short educational videos (~60s vertical shorts) from CBSE/NCERT textbooks (Classes 9-12, all streams). Daily upload target with AI TTS narration.

## Source Material
- **Primary Source:** NCERT24.com (fully accessible, no auth required)
- **Content Type:** PDF textbooks
- **Coverage:** Classes 9-12, all streams (Science, Commerce, Arts)

## Project Structure
```
cbse-youtube-channel/
├── assets/          # Media assets for videos
├── research/        # Research files, catalogues, plans
│   └── STATUS.md    # Current project status
├── remotion-app/    # Remotion project setup (needs Chromium)
├── scripts/         # Automation scripts
├── STATUS.md        # Project status and next steps
└── WORKBOARD-UPDATE.md  # Workboard tracking
```

## Current Status (July 2026)
- **Phase 1:** ✅ Research & Catalogue Complete
  - Comprehensive catalogue created with all subjects/classes verified
  - Known gaps documented (Class 9/10 Hindi, Class 12 English/Hindi lack PDFs)
  
- **Phase 2:** ⚠️ Partially Set Up
  - Remotion project structure created
  - Dependencies installed (Remotion 4.x, React 19)
  - **Issue:** No Chrome/Chromium browser installed for video rendering

## Next Steps
1. ✅ Map out all available textbooks and chapters (DONE)
2. ✅ Verify actual content availability on NCERT24.com (DONE)
3. ⬜ Set up Remotion development environment
4. ⬜ Test PDF extraction from textbook PDFs
5. ⬜ Create first sample video template
6. ⬜ Build automation pipeline for daily content generation

## Priority Subjects
1. Mathematics (Class 9-12) - Formula-heavy, most popular
2. Physics (Class 11-12) - Visual explanations needed
3. Chemistry (Class 11-12) - Reaction mechanisms, diagrams
4. Biology (Class 11-12) - Diagram-heavy content
5. History (Class 9-12) - Rich narrative, good for storytelling

## Migration Note
This project was migrated from OpenClaw workspace to Hermes on July 3, 2026.
All source data preserved at original locations.
