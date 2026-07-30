## Variant: Minimal Immersive (reader-first)

### Design stance
A clean, distraction-free PDF reader that feels like Apple Books / Google Play Books — no chrome, just the document. The toolbar auto-hides. Thumbnails are replaced by a slim page scrubber.

### Key choices
- **Layout:** Full content, toolbar auto-hides after 2s and reappears on hover/tap. Page scrubber at the bottom (draggable).
- **Mobile-first:** Single column, swipe gestures, bottom sheet toolbar. Thumbnail rail is replaced with a compact page scrubber that works one-handed.
- **PDF rendering:** Same iframe approach but with `position: sticky` toolbar that sits at the bottom edge.
- **Animations:** Smooth transitions for toolbar reveal, page transitions.
- **Loading:** Clean spinner, no skeleton.

### Trade-offs
- Strong at: mobile users, readers who just want to scan the content. Feels premium.
- Weak at: power users who need thumbnails for navigation — this variant trades thumbnail browsing for a cleaner reading experience.

### Best for
Content consumers — users who want to read the PDF quickly, not navigate between specific pages for reference.