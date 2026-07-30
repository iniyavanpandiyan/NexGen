"""
Unified PDF text extractor — one-pass, bilingual, with OCR fallback.

Handles:
- English NCERT PDFs (pypdf, fast)
- Hindi/regional PDFs with non-Unicode fonts (Tesseract OCR)
- Sanskrit PDFs (pypdf, works)
- Maths/Physics/Chemistry (pypdf + LaTeX sanitisation)

Output:
  {text, method, pages, scripts: [{title, segments: [{text}]}], sections: [{type, content}]}
"""

import re, os, hashlib, json
from pathlib import Path

# ---------------------------------------------------------------------------
#  Language detection from path
# ---------------------------------------------------------------------------
def _lang_from_path(path: str) -> str:
    """Map CBSE subject dir names to Tesseract language packs."""
    pl = path.lower()
    for kw, langs in [
        ("hindi",        "hin+san+eng"),
        ("social-science-hindi", "hin+san+eng"),
        ("sanskrit",     "san+hin+eng"),
        ("tamil",        "tam+eng"),
        ("telugu",       "tel+eng"),
        ("malayalam",    "mal+eng"),
        ("kannada",      "kan+eng"),
        ("bengali",      "ben+eng"),
        ("urdu",         "urd+eng"),
    ]:
        if kw in pl:
            return langs
    return "eng"

# ---------------------------------------------------------------------------
#  Text cleaning (common for pypdf and OCR output)
# ---------------------------------------------------------------------------
def clean_text(text: str) -> str:
    """Normalise extracted text: merge broken words, fix spacing, remove orphans.

    Handles pypdf layout artifacts (soft line breaks → spaces), OCR noise, and
    common NCERT-specific issues like colon-dangling and figure captions.
    """
    # Cache page/column runs
    text = re.sub(r"(\w)-\s*\n\s*(\w)", r"\1\2", text)    # hyphenated break
    text = re.sub(r"(?<=\w)\s*\n(?=[a-z])", "", text)       # mid-word break (pypdf space+\n)
    text = re.sub(r"(?<=[a-z0-9,;:])\n(?!\s*\n)", " ", text)  # paragraph flow
    text = re.sub(r"\s*/\s*", " ", text)                     # column-dangle "/"
    text = re.sub(r" {3,}", "  ", text)                      # heavy whitespace
    # Remove page-number orphans (isolated digit lines)
    text = re.sub(r"\n\d{1,3}\n", "\n", text)
    # Collapse triple+ newlines to double (paragraph delimiters)
    text = re.sub(r"\n{3,}", "\n\n", text)
    # Strip checkboxes / box-drawing chars pypdf inserts
    text = re.sub(r"[□■●▪▸➢➤✓✗]", "", text)

    # --- Spell-aware fix round-trip: split then join ---
    # Phase 1: Split false merges (e.g. "themselvesare" → "themselves are")
    # Phase 2: Join false splits (e.g. "envir onment" → "environment")
    text = _spell_roundtrip(text)

    return text.strip()


# ---------------------------------------------------------------------------
#  Spell-aware roundtrip: split false merges & join false splits
# ---------------------------------------------------------------------------
def _spell_roundtrip(text: str) -> str:
    """Two-pass spell-aware fix: split, then join.

    Pass 1 — Split false merges:
      For any unrecognized word, find the best split point where both halves
      are known English words. This fixes "themselvesare" → "themselves are",
      "initialsubstance" → "initial substance".

    Pass 2 — Join false splits:
      For adjacent word pairs where neither is a real word but the combined
      form IS a real word, join them. This fixes "accessib le" → "accessible",
      "envir onment" → "environment".
    """
    import enchant
    _dk = enchant.Dict("en_US")

    def _split_unknown(word: str) -> str:
        """Try to split a single unknown word into known words."""
        lo = word.lower()
        if _dk.check(lo) or len(lo) < 6:
            return word
        # Longest prefix match: find a prefix that IS a known word
        for split in range(len(lo) - 3, 3, -1):
            pre, suf = lo[:split], lo[split:]
            if _dk.check(pre):
                if _dk.check(suf):
                    # Both halves are real words!
                    return word[:split] + " " + word[split:]
                # Prefix IS a word; try to split suffix recursively
                rest = _split_unknown(word[split:])
                if rest != word[split:]:
                    return word[:split] + " " + rest
        return word

    def _join_fragments(words: list) -> list:
        """Join adjacent word fragments where combined form is a real word."""
        i = 0
        out = []
        while i < len(words):
            if i + 1 < len(words):
                a, b = words[i], words[i + 1]
                combined = a + b
                a_real = _dk.check(a)
                b_real = _dk.check(b)
                combined_real = _dk.check(combined.lower())
                # Join if: combined is a word AND at least one fragment isn't
                if combined_real and (not a_real or not b_real):
                    out.append(combined)
                    i += 2
                    continue
                # Join if: combined might be a word and neither fragment is
                # (catches "onmentwhich" → not useful alone; let splitter handle)
            out.append(words[i])
            i += 1
        return out

    def _fix_sentence(s: str) -> str:
        words = s.split()
        if len(words) < 2:
            return s
        # Pass 1: split unknown words (fix false merges)
        split_words = []
        for w in words:
            # Only try to split if the whole word isn't known
            if not _dk.check(w.lower()) and len(w) > 6:
                # Check if it's a compound of multiple known words
                parts = _split_unknown(w)
                split_words.append(parts)
            else:
                split_words.append(w)
        # Flatten: some splits returned multi-word strings
        flat = []
        for sw in split_words:
            if " " in sw:
                flat.extend(sw.split())
            else:
                flat.append(sw)
        # Pass 2: join fragments (fix false splits)
        joined = _join_fragments(flat)
        return " ".join(joined)

    sents = re.split(r"(?<=[.!?])\s+", text)
    fixed = [_fix_sentence(s) for s in sents]
    return " ".join(fixed)

# ---------------------------------------------------------------------------
#  Garbled-output detection
# ---------------------------------------------------------------------------
def _is_garbled(text: str) -> bool:
    """Return True if pypdf output looks like non-Unicode font junk.

    Uses a 3-way check (ordered by reliability):
    1. Consonant-cluster density → garbage detection
    2. Devanagari presence → clean (Sanskrit/Hindi Unicode PDFs)
    3. English dictionary-word ratio → clean
    """
    sample = text[:1500].strip()
    if not sample:
        return True

    tokens = re.findall(r"\S+", sample)
    if not tokens:
        return True
    alpha_words = [t for t in tokens if re.match(r"^[a-zA-Z]+$", t)]

    # --- 1. Consonant-cluster density ---
    # Garbage text has many 3+ consonant runs like "lkjnk", "dgfga"
    # English text has 1–2 consonant runs ("th", "str", "ght")
    if alpha_words:
        bad_clusters = 0
        for w in alpha_words:
            max_cluster = 1
            cur = 1
            for ch in w.lower():
                if ch not in "aeiouy":
                    cur += 1
                    max_cluster = max(max_cluster, cur)
                else:
                    cur = 1
            if max_cluster >= 4:
                bad_clusters += 1
        # If >20% of words have 4+ consecutive consonants → garbled
        if bad_clusters / len(alpha_words) > 0.20:
            return True
        # If >40% of tokens have zero vowels → garbled
        no_vowel = sum(1 for w in alpha_words
                       if not re.findall(r"[aeiouy]", w.lower()))
        if no_vowel / len(alpha_words) > 0.40:
            return True

    # --- 2. Devanagari (Sanskrit / Hindi Unicode PDFs are clean) ---
    devanagari = len(re.findall(r"[\u0900-\u097F]", sample))
    if devanagari > 15:
        return False

    # --- 3. Clean English word ratio (run last — garbled text also has
    #     fake "words" like "dkO" that pass the regex) ---
    clean_words = sum(1 for t in tokens if re.match(r"^[a-zA-Z]{3,}$", t))
    if clean_words / len(tokens) > 0.35:
        return False

    return True

# ---------------------------------------------------------------------------
#  Main extraction
# ---------------------------------------------------------------------------
def extract(path: str, dpi: int = 200) -> dict:
    """
    Extract all text from a PDF in one pass.

    Returns:
      text   – full cleaned text
      method – "pypdf" | "ocr" | "mixed"
      pages  – total page count
      ocr_pages – number of pages that needed OCR
      lang   – language pack used for OCR (if applicable)
    """
    from pypdf import PdfReader

    result = {"text": "", "method": "pypdf", "pages": 0, "ocr_pages": 0, "lang": ""}

    reader = PdfReader(path)
    total = len(reader.pages)
    result["pages"] = total

    # --- Phase 1: try pypdf on all pages (fast path, may have garbled text) ---
    pypdf_text = ""
    for pg in reader.pages:
        raw = pg.extract_text() or ""
        pypdf_text += raw + "\n\n"
    pypdf_text = clean_text(pypdf_text)

    # --- Phase 2: always run OCR (quality over speed) ---
    from pdf2image import convert_from_path
    import pytesseract

    lang = _lang_from_path(path)
    result["lang"] = lang

    ocr_chunks = []
    try:
        images = convert_from_path(path, dpi=dpi, fmt="jpeg", thread_count=2)
        for img in images:
            t = pytesseract.image_to_string(img, lang=lang)
            ocr_chunks.append(t)
    except Exception as e:
        # If OCR fails (no Tesseract, no disk space, etc.), fall back to pypdf
        result["text"] = pypdf_text
        result["method"] = "pypdf"
        return result

    ocr_text = clean_text("\n\n".join(ocr_chunks))

    # --- Phase 3: prefer OCR; fall back to pypdf if OCR produced nothing ---
    if ocr_text.strip():
        result["text"] = ocr_text
        result["method"] = "ocr"
        result["ocr_pages"] = total
    else:
        result["text"] = pypdf_text
        result["method"] = "pypdf"
    return result


# ---------------------------------------------------------------------------
#  Script segmentation — one-pass text → ~1-min scripts
# ---------------------------------------------------------------------------
def segment_into_scripts(text: str, words_per_script: int = 180,
                         segments_per_script: int = 6,
                         target_lang: str = "en") -> list:
    """
    Split cleaned text into ~1-minute scripts, each with sub-segments.

    words_per_script: target ~180 words ≈ 1 min at moderate speaking rate
    segments_per_script: how many editable text boxes per script

    Returns [{title, segments: [{text}...]}, ...]
    """
    if not text:
        return []

    # --- Sentence splitting (multi-script aware) ---
    # Handle Hindi/Sanskrit sentence endings (।) AND English (. ! ?)
    # Also handle line-break-separated poetry lines (common in Hindi)
    sents = re.split(r"(?<=[.!?।])\s+", text)
    # For poetry stanzas, keep lines without splitting
    sents = [s.strip() for s in sents if s.strip() and len(s.strip()) > 10]

    if not sents:
        # Fall back to block splitting
        blocks = [b.strip() for b in text.split("\n\n") if b.strip() and len(b.strip()) > 20]
        if not blocks:
            return [{"title": "Full text", "segments": [{"text": text[:2000]}]}]
        sents = blocks

    # --- Build scripts by accumulating segments ---
    scripts = []
    current_script = []
    word_count = 0

    def _script_segments(sents_list, target_count):
        """Split a list of sentences into sub-segments for one script."""
        if not sents_list:
            return []
        chunks = []
        chunk = []
        target = max(2, len(sents_list) // target_count + 1)
        for s in sents_list:
            chunk.append(s)
            if len(chunk) >= target:
                chunks.append(" ".join(chunk))
                chunk = []
        if chunk:
            chunks.append(" ".join(chunk))
        return [{"text": c} for c in chunks]

    seg_accum = []
    for s in sents:
        wc = len(s.split())
        seg_accum.append(s)
        word_count += wc

        if word_count >= words_per_script:
            scripts.append({
                "segments": _script_segments(seg_accum, segments_per_script)
            })
            seg_accum = []
            word_count = 0

    if seg_accum:
        scripts.append({
            "segments": _script_segments(seg_accum, segments_per_script)
        })

    # Add titles
    for i, sc in enumerate(scripts):
        sc["title"] = f"Script {i + 1}"
        # Try to extract a meaningful title from the first segment
        first = sc["segments"][0]["text"][:80].strip()
        sc["title"] = first[:60] + "…" if len(first) > 60 else first

    return scripts


# ---------------------------------------------------------------------------
#  CLI
# ---------------------------------------------------------------------------
if __name__ == "__main__":
    import sys, json
    path = sys.argv[1]
    r = extract(path)
    print(f"Method : {r['method']}  Pages: {r['pages']}  Chars: {len(r['text'])}")
    if r["method"] == "ocr":
        print(f"OCR    : {r['lang']}  {r['ocr_pages']}pgs")

    scripts = segment_into_scripts(r["text"])
    print(f"Scripts: {len(scripts)}")
    for i, sc in enumerate(scripts):
        print(f"\n── Script {i+1}: {sc['title'][:60]}")
        print(f"    {len(sc['segments'])} segments, ~{sum(len(s['text'].split()) for s in sc['segments'])} words")
        for j, seg in enumerate(sc["segments"]):
            print(f"    [{j+1}] {seg['text'][:120]}…")