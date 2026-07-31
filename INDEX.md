# CBSE YouTube Channel - Project Index

## Overview
Building a YouTube channel using **Remotion** to produce short educational videos (~60s vertical shorts) from CBSE/NCERT textbooks (Classes 9-12, all streams). Daily upload target with AI TTS narration.

---

## 📁 Project Structure
```
cbse-youtube-channel/
├── README.md                    # Project overview and migration notes
├── STATUS.md                    # Current status report
├── WORKBOARD-UPDATE.md          # Workboard tracking updates
├── boards/                      # Kanban boards
│   ├── KANBAN.md               # Main kanban board (DONE/IN PROGRESS/BLOCKED)
│   ├── TASKS.md                # Detailed task breakdown
│   └── SUBJECTS.md             # Complete subject catalogue
├── research/                    # Research files
│   ├── final-catalogue.md      # Complete subject catalogue with URLs
│   ├── quick-reference.md      # Quick reference guide
│   ├── STATUS.md               # Research status
│   ├── project-memory.md       # Long-term project memory
│   ├── ncert24-catalogue.md    # NCERT24.com catalogue details
│   ├── comprehensive-catalogue.md  # Comprehensive subject list
│   ├── textbook-sources.md     # Textbook source information
│   ├── cbse-textbook-info.md   # CBSE textbook information
│   ├── plan.md                 # Project plan
│   └── 2026-07-03.md           # Daily notes (July 3, 2026)
├── remotion-app/                # Remotion project setup
│   └── src/                     # Source files
├── scripts/                     # Automation scripts
├── assets/                      # Media assets
└── memory/                      # Daily notes
    └── 2026-07-03.md           # Migration day notes
```

---

## 📋 Kanban Boards

### Main Board (`boards/KANBAN.md`)
- **✅ DONE:** Research & Catalogue Phase 1 Complete
- **🔄 IN PROGRESS:** Finish Phase 1 verification, Install Chromium
- **🚧 BLOCKED:** No Chrome/Chromium browser installed

### Task Board (`boards/TASKS.md`)
- **🔴 HIGH PRIORITY:** Phase 2 setup, Content pipeline
- **🟡 MEDIUM PRIORITY:** Phase 1 completion, Video format research
- **🟢 LOW PRIORITY:** Future enhancements

### Subject Catalogue (`boards/SUBJECTS.md`)
- **✅ COMPLETE:** 76 subjects with PDFs across Classes 9-12
- **❌ INCOMPLETE:** 4 subjects missing (Class 9/10 Hindi, Class 12 English/Hindi)

---

## 📊 Project Metrics
- **Total Subjects:** ~80+ PDF textbooks across Classes 9-12
- **With Full PDFs:** 76 subjects
- **Without PDFs:** 4 subjects (Class 9/10 Hindi, Class 12 English/Hindi)

---

## 🎯 Priority Subjects
1. Mathematics (Class 9-12) - Formula-heavy, most popular
2. Physics (Class 11-12) - Visual explanations needed
3. Chemistry (Class 11-12) - Reaction mechanisms, diagrams
4. Biology (Class 11-12) - Diagram-heavy content
5. History (Class 9-12) - Rich narrative, good for storytelling

---

## 📅 Content Strategy
- **Format:** Vertical Shorts/Reels (~60s)
- **Voiceover:** AI Text-to-Speech
- **Frequency:** Daily uploads
- **Coverage:** All streams (Science, Commerce, Arts)
- **Source:** NCERT24.com

---

## 🚀 Next Steps
1. ✅ Map out all available textbooks and chapters (DONE)
2. ✅ Verify actual content availability on NCERT24.com (DONE)
3. ⬜ Set up Remotion development environment
4. ⬜ Test PDF extraction from textbook PDFs
5. ⬜ Create first sample video template
6. ⬜ Build automation pipeline for daily content generation

---

## 📝 Migration History
- **July 3, 2026:** Migrated from OpenClaw workspace to Hermes
- Original data preserved at: `/home/fiipadmin/.openclaw/workspace/cbse-youtube-channel/`
- New project location: `/home/fiipadmin/workspace/NexGen/`

---

## 🔗 Quick Links
- **Source:** https://ncert24.com
- **Video Engine:** Remotion (React-based)
- **Dependencies:** React 19, Remotion 4.x
- **Browser Needed:** Chromium (for rendering)
