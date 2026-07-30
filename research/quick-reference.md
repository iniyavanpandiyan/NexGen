# CBSE YouTube Channel - Quick Reference

## Source Material
- **Primary Source:** NCERT24.com (https://ncert24.com)
- **Content Type:** PDF textbooks
- **Access:** No authentication required
- **Last Verified:** July 2026

## URL Patterns
### PDF Textbooks (Primary Content Source)
```
https://ncert24.com/assets/ots/pdf/{filename}.pdf
```
Examples:
- `https://ncert24.com/assets/ots/pdf/Vigyan-Class-IX.pdf` (Maths Class 9)
- `https://ncert24.com/assets/ots/pdf/Biology-Class-XI.pdf` (Biology Class 11)

### Book Landing Pages (Navigation Only)
```
https://ncert24.com/ncert-books/{subject}-class-{X}-book-pdf
```

### Chapter Landing Pages (Index Only - NO ACTUAL CONTENT)
```
https://ncert24.com/ncert-books/ncert-books-class-{X}-{subject}-chapter-{Y}
```
⚠️ These are landing/index pages with chapter titles and links only. They do NOT contain actual textbook text.

## Video Format Options
1. **Quick Explainers:** 2-5 min clips on single concepts
2. **Chapter Summaries:** Break down chapters into multiple short videos
3. **Formula Highlights:** Visual formula cards with animations
4. **Problem Solving:** Step-by-step solution walkthroughs

## Priority Content Strategy
### STEM Subjects (High Demand)
1. Mathematics (Class 9-12) - Formula-heavy, most popular
2. Physics (Class 11-12) - Visual explanations needed
3. Chemistry (Class 11-12) - Reaction mechanisms, diagrams
4. Biology (Class 11-12) - Diagram-heavy content

### Social Science (Rich Narrative)
1. History (Class 9-12) - Rich narrative, good for storytelling
2. Geography (Class 11-12) - Maps, diagrams, concepts
3. Political Science (Class 11-12) - Constitution, governance
4. Economics (Class 9-12) - Concepts and examples

## Known Gaps
- Class 9 Hindi ❌
- Class 10 Hindi ❌
- Class 12 English ❌
- Class 12 Hindi ❌

## Project Structure
```
cbse-youtube-channel/
├── assets/          # Media assets for videos
├── research/        # Research files, catalogues, plans
│   ├── final-catalogue.md    # Complete subject catalogue
│   ├── quick-reference.md    # Quick reference guide
│   └── STATUS.md             # Current project status
├── remotion-app/    # Remotion project setup
├── scripts/         # Automation scripts
└── boards/          # Kanban boards (tracking)
```

## Daily Upload Target
- **Frequency:** 1 video per day
- **Format:** Vertical Shorts/Reels (~60s)
- **Voiceover:** AI Text-to-Speech
- **Coverage:** All streams (Science, Commerce, Arts)

## Tools & Technologies
- **Video Engine:** Remotion (React-based)
- **Dependencies:** React 19, Remotion 4.x
- **Browser:** Chromium (needed for rendering)
- **TTS:** AI Text-to-Speech (to be integrated)

## Next Steps
1. ✅ Map out all available textbooks and chapters (DONE)
2. ✅ Verify actual content availability on NCERT24.com (DONE)
3. ⬜ Set up Remotion development environment
4. ⬜ Test PDF extraction from textbook PDFs
5. ⬜ Create first sample video template
6. ⬜ Build automation pipeline for daily content generation
