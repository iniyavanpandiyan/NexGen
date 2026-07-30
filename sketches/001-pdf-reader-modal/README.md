## Variant: Canvas Reader (split-layout)

### Design stance
A proper PDF reader modal — like a mini Acrobat built into the dark theme. Desktop gets a spacious viewer with floating toolbar; mobile collapses to full-screen swipeable pages.

### Key choices
- **Layout:** PDF on the left, page thumbnails rail on the right (desktop). On mobile, full-width single column with bottom toolbar.
- **Controls:** Zoom (50–200%), page up/down, jump-to-page, full-screen toggle, download button.
- **PDF rendering:** Uses an iframe with PDF.js viewer for browser-native controls, wrapped in our own chrome.
- **Loading state:** Skeleton bar while PDF loads.
- **Responsiveness:** Modal goes full-screen on mobile (<640px), max 90% width/height on desktop.

### Trade-offs
- Strong at: power users who want to browse pages, zoom, search. Familiar PDF reader UX.
- Weak at: very large PDFs (100+ pages) — the thumbnail rail takes time to render.

### Best for
Technical users who need to reference specific pages from the PDF while editing scripts side-by-side.
