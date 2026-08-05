"""Phase 1a — Extract chapter index (Table of Contents) from NCERT PDFs.

Reads the first few pages of each PDF to detect chapter number and title.
Since NCERT PDFs lack bookmark outlines (PyMuPDF get_toc() returns empty),
we use Gemma vision (primary) + content-based parsing (fallback).

Output: {chapter_number: int, chapter_name: str, start_page: int} or None
"""

import json
import os
import re
import sys
from pathlib import Path
from pypdf import PdfReader

PROJECT_ROOT = Path(__file__).resolve().parents[2]
VISION_DIR = PROJECT_ROOT / "pipeline" / "lib"
if str(VISION_DIR) not in sys.path:
    sys.path.insert(0, str(VISION_DIR))

# ---------------------------------------------------------------------------
# Vision-based chapter extraction (Gemma — primary strategy)
# ---------------------------------------------------------------------------

try:
    from pipeline.lib.vision_processor import page_to_image, analyze_page
    VISION_AVAILABLE = True
except Exception:
    VISION_AVAILABLE = False


def vision_chapter_extract(path: str) -> dict | None:
    """Use Gemma vision on the first page to identify chapter name and number.

    Reads the chapter title directly from the page image (sending extracted
    text as context causes garbled results for non-English PDFs).
    Returns same format as extract_chapter() or None on failure.
    """
    if not VISION_AVAILABLE:
        return None

    try:
        img_path = page_to_image(str(path), page_num=0, dpi=200)
    except Exception:
        return None

    if not img_path:
        return None

    prompt = (
        'You are analyzing the FIRST PAGE of a CBSE/NCERT textbook chapter. '
        'Read the chapter title and number from the page image. '
        'Return ONLY valid JSON with these exact fields:\n'
        '- chapter_number: the chapter number as an integer (or null if not found)\n'
        '- chapter_name: the EXACT chapter title as written on the page, '
        'in its ORIGINAL language (Hindi, Sanskrit, English, etc.)\n'
        '- subject: the subject name (or null if not clear)\n\n'
        'IMPORTANT: Read the title from the IMAGE, do not transliterate. '
        'Return ONLY valid JSON, no other text.'
    )

    try:
        result = analyze_page(img_path, task="chapter", context_prefix=prompt)
    except Exception:
        result = {"error": "vision failed"}

    # Clean up temp image
    try:
        os.remove(img_path)
        os.rmdir(os.path.dirname(img_path))
    except OSError:
        pass

    if "error" in result:
        return None

    analysis = result.get("analysis", "")
    if not analysis:
        return None

    parsed = None
    try:
        json_match = re.search(r'\{[^{}]*\}', analysis, re.DOTALL)
        if json_match:
            parsed = json.loads(json_match.group())
    except (json.JSONDecodeError, AttributeError):
        pass

    if not parsed:
        return None

    ch_name = (parsed.get("chapter_name") or "").strip()
    if not ch_name or len(ch_name) < 4:
        return None

    # Reject if output is mostly ASCII letters (mojibake from bad encoding).
    # Genuine Devanagari/Arabic text has mostly non-ASCII chars; mojibake is ASCII-only.
    ascii_alpha = sum(1 for c in ch_name if c.isascii() and c.isalpha())
    if len(ch_name) > 4 and ascii_alpha / max(len(ch_name), 1) > 0.8:
        return None

    # Reject if output contains Cyrillic (never correct for CBSE PDF chapter names).
    if any('\u0400' <= c <= '\u04FF' for c in ch_name):
        return None

    ch_num = parsed.get("chapter_number")
    if ch_num is None:
        ch_num = chapter_from_filename(path)

    return {
        "chapter_number": ch_num or 0,
        "chapter_name": ch_name,
        "start_page": 1,
        "method": "vision",
    }


# ---------------------------------------------------------------------------
# Known chapter names — authoritative mapping for books where content detection
# produces poor results (10th edition, English, Hindi, Sanskrit, etc.)
# Format: {subject: {chapter_number: "Chapter Name"}}
# Source: NCERT official syllabus / textbook table of contents
# ---------------------------------------------------------------------------
KNOWN_CHAPTERS: dict[str, dict[int, str]] = {
    "class-10-science": {
        1: "Chemical Reactions and Equations",
        2: "Acids, Bases and Salts",
        3: "Metals and Non-metals",
        4: "Carbon and its Compounds",
        5: "Life Processes",
        6: "Control and Coordination",
        7: "How do Organisms Reproduce?",
        8: "Heredity",
        9: "Light – Reflection and Refraction",
        10: "The Human Eye and the Colourful World",
        11: "Electricity",
        12: "Magnetic Effects of Electric Current",
        13: "Our Environment",
    },
    "class-10-mathematics": {
        1: "Real Numbers",
        2: "Polynomials",
        3: "Pair of Linear Equations in Two Variables",
        4: "Quadratic Equations",
        5: "Arithmetic Progressions",
        6: "Triangles",
        7: "Coordinate Geometry",
        8: "Introduction to Trigonometry",
        9: "Some Applications of Trigonometry",
        10: "Circles",
        11: "Areas Related to Circles",
        12: "Surface Areas and Volumes",
        13: "Statistics",
        14: "Probability",
    },
    "class-10-social-science": {
        1: "Resources and Development",
        2: "Forest and Wildlife Resources",
        3: "Water Resources",
        4: "Agriculture",
        5: "Minerals and Energy Resources",
        6: "Manufacturing Industries",
        7: "Lifelines of National Economy",
        # History
        8: "The Rise of Nationalism in Europe",
        9: "Nationalism in India",
        10: "The Making of a Global World",
        11: "The Age of Industrialisation",
        12: "Print Culture and the Modern World",
        # Political Science
        13: "Power-sharing",
        14: "Federalism",
        15: "Gender, Religion and Caste",
        16: "Political Parties",
        17: "Outcomes of Democracy",
        # Economics
        18: "Development",
        19: "Sectors of the Indian Economy",
        20: "Money and Credit",
        21: "Globalisation and the Indian Economy",
    },
    "class-10-english": {
        1: "A Letter to God",
        2: "Nelson Mandela: Long Walk to Freedom",
        3: "Two Stories about Flying",
        4: "From the Diary of Anne Frank",
        5: "Glimpses of India",
        6: "Mijbil the Otter",
        7: "Madam Rides the Bus",
        8: "The Sermon at Benares",
        9: "The Proposal",
    },
    "class-10-health-and-physical-education": {
        1: "Physical Education: Relationship with other Subjects",
        2: "Effects of Physical Activities on Human Body",
        3: "Growth and Development during Adolescence",
        4: "Individual Games and Sports I",
        5: "Individual Games and Sports II",
        6: "Team Games and Sports I",
        7: "Team Games and Sports II",
        8: "Yoga for Healthy Living",
        9: "Dietary Considerations and Food Quality",
        10: "Safety Measures for Healthy Living",
        11: "Healthy Community Living",
        12: "Social Health",
        13: "Agencies and Awards Promoting Health, Sports and Yoga",
    },
    "class-9-english": {
        1: "Reflect and Respond",
        2: "The Adventures of Toto",
        3: "Iswaran the Storyteller",
        4: "In the Kingdom of Fools",
        5: "The Happy Prince",
        6: "Weathering the Storm in Ersama",
        7: "The Last Leaf",
        8: "A House is Not a Home",
        9: "The Beggar",
        10: "The Shape of Stories",
    },
    "class-9-science": {
        1: "Exploration: Entering the World of Secondary Science",
        2: "Cell: The Building Block of Life",
        3: "Tissues in Action",
        4: "Describing Motion Around Us",
        5: "Force, Work and Energy",
        6: "Earth's Dynamic Interior",
        7: "Energy: The Driving Force of the Universe",
        8: "Exploring Sound and Light",
        9: "The Language of Chemical Equations",
        10: "Sound Waves: Characteristics and Applications",
        11: "Light: Reflection and Refraction",
        12: "Electricity: Powering the Modern World",
        13: "Earth as a System: Energy, Matter, and Life",
        14: "From Food to Fuel",
    },
    "class-9-mathematics": {
        1: "Orienting Yourself: The Use of Coordinates",
        2: "Exploring Patterns: From Geometry to Numbers",
        3: "Numbers in the World Around Us",
        4: "Algebraic Expressions: Patterns and Constructions",
        5: "Linear Equations: Patterns and Modelling",
        6: "Triangles: Congruence and Inequality",
        7: "Quadrilaterals and their Properties",
        8: "Predicting What Comes Next: Exploring Sequences and Progressions",
        9: "Geometric Constructions",
        10: "Probability in Everyday Life",
        11: "Understanding Data through Graphs",
        12: "Mensuration: Length, Area, and Volume",
        13: "Solids and their Properties",
        14: "Sets and their Applications",
    },
    "class-9-social-science": {
        1: "Understanding Social Science",
        2: "Understanding Secularism",
        3: "The Constitution: A Living Document",
        4: "Understanding Elections",
        5: "Working of Institutions",
        6: "Citizen's Rights and Duties",
        7: "Understanding the Economy",
        8: "India: Physical Features",
        9: "The Price Puzzle: Understanding Inflation",
        10: "Climate of India",
        11: "Natural Vegetation and Wildlife",
        12: "Population of India",
    },
}

# ---------------------------------------------------------------------------
# Subject & class detection from path
# ---------------------------------------------------------------------------
def detect_class_subject(path: str) -> tuple[str | None, str | None]:
    """Detect class and subject from PDF path.

    Returns (class_label, subject) e.g. ("class-9", "science")
    """
    rel = Path(path).as_posix().lower()

    # Detect class
    cls_key = None
    for c in ["class-9", "class-10", "class-11", "class-12"]:
        if f"/{c}/" in rel:
            cls_key = c
            break

    # Detect subject
    subj = None
    parent_dir = Path(path).parent.name.lower()
    subj_map = [
        ("social-science", "social-science"),
        ("social", "social-science"),
        ("science", "science"), ("physics", "science"), ("chemistry", "science"),
        ("biology", "science"),
        ("mathematics", "mathematics"), ("maths", "mathematics"),
        ("health-and-physical-education", "health-and-physical-education"),
        ("health", "health-and-physical-education"),
        ("physical-education", "health-and-physical-education"),
        ("physical", "health-and-physical-education"),
        ("english", "english"),
        ("hindi", "hindi"),
        ("sanskrit", "sanskrit"),
    ]
    for k, v in subj_map:
        if k == parent_dir or parent_dir.startswith(k):
            subj = v
            break
    # If not found in directory name, check in path
    if not subj:
        for k, v in subj_map:
            if f"/{k}/" in rel or f"/{k}-" in rel:
                subj = v
                break

    return cls_key, subj


def known_chapter_name(path: str, ch_num: int | None) -> str | None:
    """Look up chapter name from the known mapping."""
    if ch_num is None:
        return None
    cls, subj = detect_class_subject(path)
    if not cls or not subj:
        return None
    key = f"{cls}-{subj}"
    mapping = KNOWN_CHAPTERS.get(key)
    if mapping and ch_num in mapping:
        return mapping[ch_num]
    return None


# ---------------------------------------------------------------------------
# Shared name-normalization / page-header detection
# (also used by main.py's per-page aggregation)
# ---------------------------------------------------------------------------
def normalize_chapter_name(name: str) -> str:
    """Lowercase and collapse whitespace so OCR spelling variants of the
    same title count as one ('Effects of Physical activities on human Body'
    and 'Effects of Physical Activities on Human Body' both become one key)."""
    name = re.sub(r"\s+([:;,])", r"\1", name)  # 'INDIA : PHYSICAL' -> 'INDIA: PHYSICAL'
    return re.sub(r"\s+", " ", name.strip().lower())


def looks_like_page_header(name: str) -> bool:
    """True if the candidate is a book running header (subject title + class)
    rather than a chapter title — e.g. 'Health and Physical Education - Class X'."""
    low = normalize_chapter_name(name)
    if len(low) < 10:
        return False
    # Rotated headers render glyph-spaced ('H ealth and P hysical ...'); test
    # both the raw form and the de-spaced form so we catch them.
    compact = re.sub(r"\s+", "", low)
    lows = (low, compact)
    if any(re.search(r"class\s*[ivxlcdm]+\s*$", x) for x in lows):
        return True
    if any(re.search(r"[-–—]\s*class\s*[ivxlcdm]+", x) for x in lows):
        return True
    if any(re.search(r"(?:health|science|mathematics|maths|social\s*science|english|hindi)\s*and\s*", x) for x in lows):
        return True
    # A bare subject name ('mathematics', 'science') is a book header, not a
    # chapter title. Only reject exact/subject-only matches so real chapter
    # titles like 'Mathematics in Everyday Life' are not dropped.
    if any(re.fullmatch(r"(?:mathematics|maths|science|social\s*science|health\s*and\s*physical\s*education|english|hindi|sanskrit)", x) for x in lows):
        return True
    return False


def _is_book_header(name: str) -> bool:
    """True if the candidate is a book/subject-level running header rather than
    a chapter title. NCERT books print the subject or book title at the top of
    every page (e.g. 'Physics', 'MATHEMATICS', 'THEMES IN WORLD HISTORY'),
    which repeats identically across every chapter and is not a chapter title."""
    low = normalize_chapter_name(name)
    compact = re.sub(r"\s+", "", low)
    lows = (low, compact)
    if any(re.fullmatch(r"(physics|chemistry|biology|mathematics|maths|science|geography|history|english|hindi|sanskrit|urdu|social science|physical education)", x) for x in lows):
        return True
    # Whole-book series titles that are printed on every page. Compare in
    # compacted form too, since headers render as 'INDIA : PHYSICAL
    # ENVIRONMENT' with a space before the colon.
    book_titles = [
        "themesinworldhistory",
        "themesinindianhistory",
        "fundamentalsofphysicalgeography",
        "india:physicalenvironment",
        "indiaphysicalenvironment",
        "contemporaryworldpolitics",
        "indianeconomicdevelopment",
        "introductorymacroeconomics",
        "statisticsforeconomics",
    ]
    for bt in book_titles:
        if any(bt in x for x in lows):
            return True
    return False


# Cache of per-directory shared (book-level) header names, computed lazily.
_book_header_cache: dict[str, set[str]] = {}

def _shared_book_headers(path: str) -> set[str]:
    """Return normalized header strings that appear identically in multiple
    chapter files of the same book directory. A running header that repeats
    across chapters is the book title (e.g. 'Kaveri', 'Madhurima'), not a
    chapter title, so it must not be returned as a chapter name."""
    import glob as _glob

    directory = os.path.dirname(os.path.abspath(path))
    if directory in _book_header_cache:
        return _book_header_cache[directory]

    winners: dict[str, list[str]] = {}
    for sibling in _glob.glob(os.path.join(directory, "*.pdf")):
        winner = _page_header_winner(sibling)
        if winner is None:
            continue
        winners.setdefault(normalize_chapter_name(winner), []).append(winner)

    shared: set[str] = set()
    for norm, titles in winners.items():
        if len(titles) >= 2:
            shared.add(norm)
    _book_header_cache[directory] = shared
    return shared


def _page_header_winner(path: str, skip_normalized: str | None = None) -> str | None:
    """Scan a single PDF's running headers and return the majority title, or
    None. Lower-level helper used for shared-header detection; does not apply
    the shared-header exclusion itself. If ``skip_normalized`` is given, that
    normalized title is excluded from the vote (used to demote a book title)."""
    try:
        import fitz
    except ImportError:
        return None
    try:
        doc = fitz.open(path)
    except Exception:
        return None
    total = len(doc)
    if total == 0:
        doc.close()
        return None

    candidates = []
    for pg in range(total):
        try:
            page = doc[pg]
            pw, ph = page.rect.width, page.rect.height
            # Books rendered through legacy non-Latin fonts (e.g. Devanagari
            # via 'Chanakya') have a garbage Latin text layer — skip the whole
            # page so the vision strategy handles the script properly.
            page_fonts = [f[3] for f in page.get_fonts()]
            if any(_is_nonlatin_font(f) for f in page_fonts):
                continue
            blocks = page.get_text("blocks")
        except Exception:
            continue
        if not blocks:
            continue
        margin_blocks = []
        top_blocks = []
        for blk in blocks:
            x0, y0, x1, y1, text = blk[0], blk[1], blk[2], blk[3], (blk[4] or "")
            raw = re.sub(r"\s+", " ", text.strip())
            if len(raw) < 4:
                continue
            in_top = y1 < 0.14 * ph
            in_left_margin = x1 < 0.18 * pw
            in_right_margin = x0 > 0.82 * pw
            if in_left_margin or in_right_margin:
                margin_blocks.append((y0, y1, raw))
            elif in_top:
                top_blocks.append((y0, y1, raw))

        def _first_header(region_blocks):
            """Return the filtered top-most header candidate for a region, or
            None. Merges consecutive blocks that share one header line."""
            if not region_blocks:
                return None
            region_blocks.sort()
            merged = []
            for y0, y1, raw in region_blocks:
                if merged and y0 - merged[-1][0] < 4:
                    prev_y1, prev = merged[-1]
                    merged[-1] = (max(prev_y1, y1), prev + " " + raw)
                else:
                    merged.append((y1, raw))
            cand = merged[0][1]
            cand = re.sub(r"\s+", " ", cand)
            if len(cand) < 6:
                return None
            cand = re.sub(r"^\s*\d+\s*[|\-–—•]?\s*", "", cand)
            cand = re.sub(r"\s*[|\-–—•]?\s*\d+\s*$", "", cand)
            if len(cand) < 6:
                return None
            if re.search(r"\d{4}\s*-\s*\d{2}$", cand) or "Reprint" in cand:
                return None
            if _is_callout(cand):
                return None
            cand = cand.rstrip(".,;:")
            if not re.search(r"[A-Za-z]{2,}", cand):
                return None
            if looks_like_page_header(cand) or _is_book_header(cand):
                return None
            if _looks_garbled(cand):
                return None
            return cand

        # Prefer the vertical (rotated) margin header — in HPE books that is
        # the chapter running title printed down the page edge, while the top
        # band holds section headings that repeat on every page. Fall back to
        # the top band only when there is no valid margin header.
        cand = _first_header(margin_blocks)
        if cand is None:
            cand = _first_header(top_blocks)
        if cand is None:
            continue
        if skip_normalized and normalize_chapter_name(cand) == skip_normalized:
            continue
        candidates.append(cand)

    doc.close()
    if not candidates:
        return None

    from collections import Counter
    counts: Counter = Counter()
    longest = {}
    for c in candidates:
        norm = normalize_chapter_name(c)
        counts[norm] += 1
        if len(c) > len(longest.get(norm, "")):
            longest[norm] = c
    best_norm, best_count = counts.most_common(1)[0]
    if best_count < max(2, total // 4):
        return None
    return longest[best_norm]


def running_header_chapter_name(path: str) -> dict | None:
    """Determine the chapter title from the page running headers.

    NCERT textbooks print the chapter title at the top of each content page
    (the running header). This is deterministic — it reads the document
    directly instead of trusting a curated map. Works even when the title is
    rendered as an image (not extractable by pypdf) because we use fitz on
    the text layer; for image-only headers we fall back to other strategies.

    Returns {chapter_number, chapter_name, start_page, method: 'header'}
    or None if no confident running-header title can be found.
    """
    ch_num_from_file = chapter_from_filename(path)

    # Majority title from this document's running headers.
    winner = _page_header_winner(path)
    if winner is None:
        return None

    # A header that repeats across chapters is the book title, not the chapter
    # title — e.g. 'Kaveri' or 'Madhurima' printed on every page. When that
    # happens, return the strongest chapter-level candidate instead.
    shared = _shared_book_headers(path)
    if normalize_chapter_name(winner) in shared:
        fallback = _page_header_winner(path, skip_normalized=normalize_chapter_name(winner))
        if fallback is None:
            return None
        winner = fallback

    title = _despace_glyph_title(winner)
    return {
        "chapter_number": ch_num_from_file or 0,
        "chapter_name": title,
        "start_page": 1,
        "method": "header",
    }


def _is_nonlatin_font(font_name: str) -> bool:
    """True if a font is a legacy non-Latin script font whose text layer maps
    glyphs to Latin letters (e.g. Devanagari via 'Walkman-Chanakya905')."""
    low = (font_name or "").lower()
    nonlatin_hints = [
        "chanakya", "nirmala", "kokila", "mangal", "lohit", "aparajita",
        "gargi", "utsaah", "tunga", "vrinda", "gautami", "shruti", "devanagari",
    ]
    return any(h in low for h in nonlatin_hints)


def _looks_garbled(name: str) -> bool:
    """True if a header decodes to non-Latin garbage rather than a real title.

    Some NCERT books embed Devanagari through legacy non-Unicode fonts (e.g.
    'Walkman-Chanakya905'), whose text layer maps glyphs to Latin letters. The
    result looks like English but is not ('lkuk&lkuk gkFk tksfM+---'). These
    should not be returned as chapter titles — the vision strategy reads the
    rendered page and handles the script properly."""
    low = normalize_chapter_name(name)
    compact = re.sub(r"[^a-z]", "", low)
    if len(compact) < 6:
        return False
    # Legacy Devanagari fonts litter titles with punctuation and shell-style
    # characters that never appear in real Latin titles.
    suspicious = re.findall(r"[{}\[\]+`\"'=@#$%^*<>~\\/|]", low)
    if len(suspicious) >= 1:
        return True
    # Mixed lower+upper with no real capitalization pattern, e.g. 'gkFk'.
    if re.search(r"\b[a-z]+[A-Z][a-z]+\b", name):
        return True
    return False


def _is_callout(name: str) -> bool:
    """True if the candidate is a common box/sidebar label, not a title."""
    low = re.sub(r"\s+", "", normalize_chapter_name(name))
    callouts = [
        "doyouknow", "moretoknow", "thinkitover", "activity", "didyouknow",
        "remember", "note", "important", "let'sdo", "letsdo", "experiment",
        "doyouknow?", "amazingfact", "wordscareful", "keywords", "note",
    ]
    for c in callouts:
        if low.startswith(c) or low == c:
            return True
    return False


def _despace_glyph_title(name: str) -> str:
    """Collapse the wide letter-spacing of rotated running headers.

    Rotated PDF text renders as 'E ffects of P hysical A ctivities', with a
    single letter separated from the rest of each word. Re-join those so the
    final chapter name is clean: 'Effects of Physical Activities'."""
    if not name:
        return name
    # Remove spaces between a single letter and a following word chunk that
    # continues in lowercase (glyph spacing): 'E ffects' -> 'Effects',
    # 'P hysical' -> 'Physical', 'I ndividual' -> 'Individual'. Words that
    # legitimately start with a capital (e.g. 'a System') are left alone.
    fixed = re.sub(r"\b([A-Za-z])\s(?=[a-z]{2,}\b)", r"\1", name)
    fixed = re.sub(r"\s+([:;,.])\s*", r"\1 ", fixed)
    fixed = re.sub(r"\s{2,}", " ", fixed).strip()
    return fixed

# ---------------------------------------------------------------------------
# Chapter number from filename (fallback)
# ---------------------------------------------------------------------------
def chapter_from_filename(path: str) -> int | None:
    """Extract chapter number from NCERT filename like iesc_ch04.pdf → 4."""
    m = re.search(r"ch(\d+)", Path(path).stem, re.I)
    return int(m.group(1)) if m else None


def title_from_filename(path: str) -> str:
    """Fallback title — humanize the filename."""
    stem = Path(path).stem
    stem = re.sub(r"^[a-z]{2,4}_ch\d+", "", stem)
    stem = stem.replace("_", " ").replace("-", " ").strip()
    if not stem:
        return f"Chapter {chapter_from_filename(path) or ''}"
    return stem.title()


# ---------------------------------------------------------------------------
# Subject-specific content patterns
# ---------------------------------------------------------------------------
SCIENCE_PATTERNS = [
    # 10th edition: "Chemical Reactions\nand Equations\n1CHAPTER"
    (r"^([A-Z][\w\s]{2,60}(?:\s*\n\s*[A-Za-z][\w\s]{2,60})?)\s*\n\s*\d+CHAPTER", None, 1),
    # 9 NCF: title with colon, then Chapter keyword
    # "Sound Waves:\nCharacteristics and\nApplications\nChapter\n10"
    (r"^([A-Z][a-zA-Z][^-\n]{3,60}:\s*(?:\n?\s*[A-Za-z][a-zA-Z][^-\n]{0,80}){0,3})\s*\n\s*Chapter\s*\n\s*(\d+)", 2, 1),
    # 9 NCF: title BEFORE "Chapter\nN" on same page
    # "Describing Motion\nAround Us\nChapter\n4"
    (r"^([A-Z][a-zA-Z][^-\n]{3,50}(?:\s*\n\s*[A-Za-z][a-zA-Z][^-\n]{3,60})?)\s*\n\s*Chapter\s*\n\s*(\d+)", 2, 1),
    # 9 NCF: "Tissues in Action\nLife begins..." — single-line title, no Chapter keyword
    (r"^([A-Z][a-zA-Z][^-\n]{3,60})\s*\n\s*(?:Life\s|In the|Have you|Researchers|If you)", None, 1),
    # 9 NCF: "Exploration: Entering the World of Secondary Science\n1\n..." — but NOT "Chapter\n13"
    (r"^(?!Chapter\s*$)([A-Z][a-zA-Z][^-\n]{3,80})\s*\n\s*(\d+)\s*$", 2, 1),
    # 9 NCF: "Cell: The Building Block of Life\n9\nfunctions forms..." — title with colon on page 2+
    (r"^([A-Z][a-zA-Z][^-\n]{3,60}:\s*(?:\n?\s*[A-Za-z][a-zA-Z][^-\n]{0,80}){0,3})\s*\n\s*(\d+)\s*\n", 2, 1),
    # 9 NCF: "Earth as a System:\nEnergy, Matter, and Life\nThink It Over" — no chapter number, get from filename
    (r"^([A-Z][a-zA-Z][^-\n]{3,60}:\s*(?:\n?\s*[A-Za-z][a-zA-Z][^-\n]{0,80}){0,3})\s*\n\s*(?:Think It Over|Activity|In this chapter)", None, 1),
    # "Chapter" on its own line, then number, then title on next lines
    # NOTE: only match if the captured title looks like a real title (not garbage text)
    (r"Chapter\s*\n+\s*(\d+)\s*\n+\s*([A-Z][a-zA-Z][^-\n]{3,80}?)(?:\n|$)", 1, 2),
]

MATHS_PATTERNS = [
    # "Predicting What Comes Next:\nExploring Sequences and\nProgressions\n8.1 Introduction"
    (r"^([A-Z][a-zA-Z][^-\n]{5,60}(?::(?:\n?\s*[A-Za-z][a-zA-Z][^-\n]{0,60}){0,3})?)\s*\n\s*\d+\.\d+\s", None, 1),
    # "REAL NUMBERS 1\n1.1 Introduction"
    (r"^([A-Z][A-Z\s]{3,60})\s+(\d+)\s*\n", 2, 1),
]

HPE_PATTERNS = [
    # "Physical Education: Relationship with Other Subjects\n3\nSocial efficiency"
    # BUT reject Fig./Table captions
    (r"^(?!Fig(?:ure)?\.?\s+\d)(?!Table\s+\d)([A-Z][a-zA-Z][^-\n]{5,60}:\s*(?:\n?\s*[A-Za-z][a-zA-Z][^-\n]{0,80}){0,3})\s*\n\s*\d+\s*\n", None, 1),
    # "Organ systems for movement of body and limbs:\n1 Principles of..."
    (r"^(?!Fig(?:ure)?\.?\s+\d)(?!Table\s+\d)([A-Z][a-zA-Z][^-\n]{5,60}:\s*(?:\n?\s*[A-Za-z][a-zA-Z][^-\n]{0,80}){0,3})\s*\n\s*\d+\s", None, 1),
]

SOCIAL_PATTERNS = [
    # 9 NCF: "Understanding Social Science\nChapter 1"
    (r"^([A-Z][a-zA-Z][^-\n]{5,80})\s*\n\s*Chapter\s+(\d+)", 2, 1),
    # CHAPTER keyword
    (r"CHAPTER\s+(\d+)\s*\n+\s*(.+?)(?:\n|$)", 1, 2),
    (r"^\s*\n*\s*Chapter\s+(\d+)\s*\n+\s*(.+?)(?:\n|$)", 1, 2),
]

# Patterns for detecting a "title-like" line on first text-bearing page
TITLE_DETECTORS = [
    # Colons (Title: Subtitle)
    r"^[A-Z][a-zA-Z][^-\n]{5,60}:",
    # Uppercase phrase (e.g., "CHEMICAL REACTIONS AND EQUATIONS")
    r"^[A-Z][A-Z\s]{5,60}",
    # Title-case line followed by activity prompt
    r"^[A-Z][a-z][A-Za-z\s]{5,50}\s*\n\s*(?:Think It Over|Activity|Have you)",
]

# Remove title-like lines that are actually document headers or garbage
SKIP_TITLES = {"page", "chapter", "contents", "index", "introduction", "preface",
               "acknowledgements", "about this book", "learning objectives"}

# Known subject names to reject as heuristic chapter titles
KNOWN_SUBJECT_NAMES = {
    "physical education", "physical education:", "social science", "social science:",
    "mathematics", "mathematics:", "science", "science:", "english", "english:",
    "hindi", "hindi:", "sanskrit", "sanskrit:",
}

# Patterns that indicate a line is NOT a chapter title
REJECT_TITLE_PATTERNS = [
    r"^Table\s+\d",        # Table 1, Table 4.1
    r"^Fig(?:ure)?\.?\s+\d",  # Fig 1, Figure 1
    r"^The\s+events?\s",   # "The events played in badminton are:"
    r"^List\s+(of|the)",   # "List of ..."
    r"^\d+[\.\s]",         # Starts with number
    r"^[A-Z][a-z]+\s+[a-z]",  # "HealtH and PHysical education" — mixed case content text
    r"^[A-Z][a-z]+\s*\([A-Z]",  # "Physical (PE) aims" — content with acronym
]

# ---------------------------------------------------------------------------
# Content-based chapter extraction
# ---------------------------------------------------------------------------
def extract_chapter(path: str) -> dict | None:
    """Extract chapter number and title from a single NCERT PDF.

    Strategy:
    1. Known chapter name mapping (curated, authoritative)
    2. Gemma vision on first page (text + image cross-reference)
    3. Subject-specific content patterns on first 4 pages
    4. Content-based heuristics (title-like line)
    5. Last resort: filename-based

    Returns: {chapter_number: int, chapter_name: str, start_page: int, method: str}
    """
    try:
        reader = PdfReader(path)
    except Exception:
        return None

    total_pages = len(reader.pages)
    cls, subj = detect_class_subject(path)

    ch_num_from_file = chapter_from_filename(path)

    # Strategy 1: Running-header title read directly from the document.
    # This is the most authoritative source — the chapter title is printed at
    # the top of every content page. Curated maps can go stale, but the
    # printed header cannot.
    header_result = running_header_chapter_name(path)
    if header_result:
        # If the header title matches a curated known-map title
        # (case-insensitively), keep the curated casing for consistency
        # (e.g. headers render all-caps, maps store title case).
        known = known_chapter_name(path, header_result.get("chapter_number") or ch_num_from_file)
        if known and normalize_chapter_name(known) == normalize_chapter_name(header_result["chapter_name"]):
            header_result["chapter_name"] = known
        return header_result

    # Strategy 2: Known chapter name (curated fallback)
    known = known_chapter_name(path, ch_num_from_file)
    if known:
        return {
            "chapter_number": ch_num_from_file or 0,
            "chapter_name": known,
            "start_page": 1,
            "method": "known",
        }

    # Strategy 3: Gemma vision on first page (accurate for unknown books)
    vision_result = vision_chapter_extract(path)
    if vision_result:
        return vision_result

    # Select patterns based on subject
    if subj == "science":
        patterns = SCIENCE_PATTERNS
    elif subj == "mathematics":
        patterns = MATHS_PATTERNS
    elif subj == "social-science":
        patterns = SOCIAL_PATTERNS
    elif subj == "health-and-physical-education":
        patterns = HPE_PATTERNS
    else:
        patterns = SOCIAL_PATTERNS  # generic fallback

    # Strategy 3: Try content patterns on first 4 pages
    for pg_idx in range(min(4, total_pages)):
        try:
            txt = reader.pages[pg_idx].extract_text() or ""
        except Exception:
            continue

        for pattern, ch_group, title_group in patterns:
            m = re.search(pattern, txt, re.MULTILINE)
            if m:
                ch_num = int(m.group(ch_group)) if ch_group else ch_num_from_file
                title = None
                if title_group:
                    title = m.group(title_group).strip()
                elif ch_group:
                    title = m.group(1).strip() if m.lastindex and m.lastindex >= 1 else None

                if title:
                    title = re.sub(r"\s+", " ", title).strip()
                    title = title.rstrip(".,;:")
                    title = re.sub(r"\s+([,;.])", r"\1", title)
                    return {
                        "chapter_number": ch_num or 0,
                        "chapter_name": title,
                        "start_page": pg_idx + 1,
                        "method": "content",
                    }

    # Strategy 3: Heuristic — find the first "title-like" line
    for pg_idx in range(min(4, total_pages)):
        try:
            txt = reader.pages[pg_idx].extract_text() or ""
        except Exception:
            continue
        raw_lines = txt.split("\n")
        lines = [l.strip() for l in raw_lines if l.strip()]
        line_map = [l for l in raw_lines if l.strip()]  # parallel list with original whitespace stripped
        for idx, ln in enumerate(lines):
            # Skip if matches any reject pattern
            if any(re.match(rp, ln) for rp in REJECT_TITLE_PATTERNS):
                continue
            # Skip if exactly a known subject name (but allow if followed by colon + continuation)
            if ln.lower().strip() in KNOWN_SUBJECT_NAMES:
                # If it's a subject name with a colon, check for continuation lines
                if not ln.rstrip().endswith(':'):
                    continue
                # Check if next line continues (e.g. "Physical Education: Relationship with...")
                has_continuation = False
                for ci in range(idx + 1, min(idx + 3, len(lines))):
                    next_ln = lines[ci]
                    if re.match(r'^[A-Za-z][a-zA-Z][a-zA-Z\s]{2,60}$', next_ln):
                        has_continuation = True
                        break
                    if re.match(r'^\d+$', next_ln):
                        break
                if not has_continuation:
                    continue
            for detector in TITLE_DETECTORS:
                if re.match(detector, ln):
                    skip = any(s in ln.lower() for s in SKIP_TITLES)
                    if not skip and len(ln) > 10 and len(ln) < 100:
                        # Join continuation lines (lines that continue the title)
                        # A continuation line starts with a capital letter, is short,
                        # and doesn't end with a period or look like a new sentence
                        title_parts = [ln]
                        for ci in range(idx + 1, min(idx + 4, len(lines))):
                            next_ln = lines[ci]
                            # Stop if line contains a complete sentence (period + capital)
                            # or is a standalone number (page number)
                            if re.match(r'^\d+$', next_ln):
                                break
                            if len(next_ln) > 80:
                                break
                            if next_ln.endswith('.') and len(next_ln) > 30:
                                break
                            # Accept if it starts with a capital letter and has at least 3 letters
                            if re.match(r'^[A-Za-z][a-zA-Z][a-zA-Z\s]{2,60}$', next_ln):
                                title_parts.append(next_ln)
                            else:
                                break
                        full_title = " ".join(title_parts)
                        full_title = re.sub(r"\s+", " ", full_title).strip()
                        full_title = full_title.rstrip(".,;: ")
                        if len(full_title) >= 5:
                            return {
                                "chapter_number": ch_num_from_file or 0,
                                "chapter_name": full_title,
                                "start_page": pg_idx + 1,
                                "method": "heuristic",
                            }

    # Strategy 4: Filename fallback
    if ch_num_from_file:
        known2 = known_chapter_name(path, ch_num_from_file)
        return {
            "chapter_number": ch_num_from_file,
            "chapter_name": known2 or f"Chapter {ch_num_from_file}",
            "start_page": 1,
            "method": "filename",
        }

    return None


# ---------------------------------------------------------------------------
# Bulk: scan all PDFs and update catalogue with chapter info
# ---------------------------------------------------------------------------
def update_catalogue_chapters(conn=None, force: bool = False) -> list[dict]:
    """Scan all PDFs in the database, extract chapter info, and return results.

    If conn is provided, updates the pdfs table with chapter_name and chapter_number columns.
    Use force=True to re-extract even entries that already have chapter data.
    """
    if not conn:
        from db import get_conn, commit
        conn = get_conn()
        own_conn = True
    else:
        own_conn = False

    # Ensure columns exist
    for col in ["chapter_number INTEGER", "chapter_name TEXT"]:
        try:
            conn.execute(f"ALTER TABLE pdfs ADD COLUMN {col}")
        except Exception:
            pass

    rows = conn.execute(
        "SELECT id, path, chapter_number, chapter_name FROM pdfs"
    ).fetchall()

    updated = []
    for r in rows:
        if r["chapter_number"] and r["chapter_name"] and not force:
            continue
        info = extract_chapter(r["path"])
        if info:
            conn.execute(
                "UPDATE pdfs SET chapter_number=?, chapter_name=? WHERE id=?",
                (info["chapter_number"], info["chapter_name"], r["id"]),
            )
            updated.append({"id": r["id"], "path": r["path"], **info})
        else:
            updated.append({"id": r["id"], "path": r["path"], "error": "not found"})

    if own_conn:
        conn.commit()
        conn.close()

    return updated


# ---------------------------------------------------------------------------
# API endpoint helper
# ---------------------------------------------------------------------------
def api_extract_chapter(path: str) -> dict:
    """Extract chapter info, suitable for API responses."""
    result = extract_chapter(path)
    if result is None:
        return {"error": "Could not extract chapter info"}
    return result


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------
if __name__ == "__main__":
    import sys, json
    if len(sys.argv) > 1:
        path = sys.argv[1]
        result = extract_chapter(path)
        print(json.dumps(result, indent=2))
    else:
        print("Usage: python toc_extractor.py <pdf_path>")
        print("  or:  python toc_extractor.py --all")
        if "--all" in sys.argv:
            updates = update_catalogue_chapters(force="--force" in sys.argv)
            print(f"Updated {len(updates)} PDFs with chapter info")
            print(json.dumps(updates[:5], indent=2))
            if len(updates) > 5:
                print(f"... and {len(updates) - 5} more")