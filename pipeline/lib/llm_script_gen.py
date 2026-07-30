"""LLM-powered script generation from PDF raw text.

Sends raw extracted text to an LLM (via OpenRouter) to:
1. Fix typos, formatting, and OCR artifacts
2. Generate structured ~1-min scripts with ~5-8 sub-segments each
3. Remove exam boilerplate, page numbers, and NCERT layout cruft
"""

import os, json, re
from pathlib import Path
from typing import Optional
import httpx

# Try loading .env for the API key — but only if the env var isn't already set
OPENROUTER_KEY = os.environ.get("OPENROUTER_API_KEY", "")
if not OPENROUTER_KEY:
    try:
        from dotenv import load_dotenv
        # Load from project root .env and fallback to ~/.hermes/.env
        project_env = Path(__file__).resolve().parents[2] / ".env"
        if project_env.exists():
            load_dotenv(project_env, override=False)
        else:
            hermes_env = Path.home() / ".hermes" / ".env"
            if hermes_env.exists():
                load_dotenv(hermes_env, override=False)
    except ImportError:
        pass
    OPENROUTER_KEY = os.environ.get("OPENROUTER_API_KEY", "")
OPENROUTER_MODEL = os.environ.get("LLM_SCRIPT_MODEL", "deepseek/deepseek-v4-flash")
OPENROUTER_BASE = "https://openrouter.ai/api/v1"

SYSTEM_PROMPT = """You are an expert NCERT content editor and visual director for CBSE educational YouTube Shorts. Your goal is to write short-form videos (approx 60 seconds each) that are **engaging, visually rich, and addictive** — viewers should feel compelled to keep watching the next script (scroll for more).

## Script Structure Rules

### 1. Hook & Cliffhanger (CRITICAL)
- **Every first segment of every script MUST begin with a hook** — a question, a surprising fact, or a relatable observation that grabs attention in the first 3 seconds. Examples: "Did you know that the steel in your tawa was once just rusty iron ore?" or "Have you ever wondered why some chemical reactions heat up while others cool down?"
- **Every final segment of every script MUST end with a cliffhanger** — a teaser that makes the viewer *need* to watch the next script. Examples: "But what happens if we add another metal to the mix? That's coming up next." or "So we've seen how atoms rearrange — but how do we actually write it down? Let's find out."
- Exception: The very first script's hook can be a broad chapter teaser. The very last script's cliffhanger should be a chapter wrap-up that hints at the next chapter's topic.

### 2. More Segments = More Cuts
- Split each script into **8-12 sub-segments** (not 5-8). Each segment is 1-2 sentences maximum — a single beat of thought. This creates more cuts, more visual variety, and a faster rhythm.
- Segments should break on natural visual beats: every time the topic shifts slightly (new example, new activity step, new concept), split into a new segment.
- For activities: each step is its own segment with its own image prompt (setup → observation → result → explanation).

### 3. Title Rules
- 5-12 words, descriptive and curiosity-driven. Use a question or a bold statement.
- Examples: "Why Does a Nail Turn Brown? (The Rusting Mystery)", "Burning Magnesium: A Dazzling White Flame", "The Law That Stops Atoms From Vanishing"

### 4. Image Prompt Rules (must be FULLY self-contained)
Each segment's `image_prompt` must be a complete, coherent description that a text-to-image model can generate faithfully. Never assume context from previous segments.

Requirements:
- **30-60 words each**, rich in visual detail
- Style prefix: "Flat vector educational illustration. Clean white background, vibrant colors, modern style."
- Include ALL of: objects, people (if relevant), spatial layout, labels/annotations, colors, arrows, and the emotion or action
- For activities: show the exact setup (apparatus, chemicals, steps in progress, observation)
- For science: show labeled diagrams with directional arrows, chemical formulas as text labels in the image
- For math: show equations on blackboard or graph paper, geometric figures with colorful highlights
- For social studies: show maps, timelines, cultural scenes with period-appropriate details
- Ensure the prompt describes a SINGLE coherent scene, not a collage of disconnected elements
- Keep safe-for-all-ages (no violence, gore, religious imagery)

Example of a GOOD image prompt:
"Flat vector educational illustration. Beaker with bubbling acid solution, zinc granules at bottom, hydrogen gas bubbles rising. Hand wearing protective glove touching the beaker. Labels 'Zinc granules' and 'Dilute HCl' with arrows pointing to each. Modern flat style, white background, vibrant blue and orange colors."

## Content Rules
- **Clean the text lightly**: Merge mid-word OCR spaces. Remove page numbers, "Reprint 2024-25", copyright lines, stray hyphenation. Keep NCERT headers/footers that indicate Activity/Think It Over sections.
- **Preserve EVERYTHING**: Do NOT strip side-content boxes — "Think It Over", "Activity", "Hint", "Did You Know?", "Try This", "Let us explore", margin notes, callouts, and tips. These are the most engaging parts for a video script.
- **Mark side-content explicitly**: When a segment comes from a side-activity or callout, prefix the text with `[ACTIVITY]`, `[THINK IT OVER]`, `[HINT]`, or `[DID YOU KNOW]` as appropriate. This cues the video editor to add visual emphasis.
- **Preserve NCERT voice**: Keep factual accuracy, scientific definitions, formulas, equations intact. Do NOT add concepts not in the text.
- **Remove exam/Q&A sections only**. Keep ALL Activity worked examples.
- **~150-200 words per script total** (spread across 8-12 segments).
- **Maximum 10 scripts** (shorter chapters may produce fewer — that's fine).
- **Every segment MUST have both "text" and "image_prompt"** — no exceptions.

## Output Format

{
  "scripts": [
    {
      "title": "Curiosity-Driven Title Here?",
      "segments": [
        {"text": "Hook sentence that grabs attention...", "image_prompt": "Flat vector educational illustration. [Rich visual description with full context, labels, layout, colors]. Modern style, white background."},
        {"text": "Second beat of the thought...", "image_prompt": "Flat vector educational illustration. [Completely self-contained visual description for this segment]."},
        {"text": "Cliffhanger that teases the next script...", "image_prompt": "Flat vector educational illustration. [Visual suggesting the unanswered question or next topic]."}
      ]
    }
  ]
}

IMPORTANT REMINDERS:
- EVERY script starts with a hook, ends with a cliffhanger
- 8-12 segments per script (more cuts = more engaging)
- Every image prompt is fully self-contained (30-60 words)
- Activities: each step gets its own segment with its own visual
- Max 10 scripts total"""


def clean_and_generate(raw_text: str, pdf_title: str = "", pdf_class: str = "",
                       pdf_subject: str = "", max_scripts: int = 12,
                       diagram_context: str = "") -> list:
    """Clean PDF text and generate structured scripts via LLM.

    Uses chunking for long texts: splits into 6000-char chunks with overlap,
    generates ~3 scripts per chunk, then merges all results.
    """
    if not raw_text or len(raw_text.strip()) < 50:
        return []

    if not OPENROUTER_KEY:
        return _rule_based_fallback(raw_text, pdf_title)

    # Pre-clean the full text
    raw_text = _pre_clean_text(raw_text)

    # Chunk the text for better LLM compliance
    CHUNK_SIZE = 6000   # Characters per chunk
    CHUNK_OVERLAP = 800  # Overlap between chunks
    SCRIPTS_PER_CHUNK = 3

    chunks = _chunk_text(raw_text, CHUNK_SIZE, CHUNK_OVERLAP)
    # Cap chunks to avoid too many LLM calls
    max_chunks = min(len(chunks), 4)
    chunks = chunks[:max_chunks]

    all_scripts = []
    context_parts = []
    if pdf_title:
        context_parts.append(f"Title: {pdf_title}")
    if pdf_class:
        context_parts.append(f"Class: {pdf_class}")
    if pdf_subject:
        context_parts.append(f"Subject: {pdf_subject}")
    context_line = " | ".join(context_parts)
    if context_line:
        context_line = f"[{context_line}]\n\n"

    for chunk_idx, chunk in enumerate(chunks):
        is_first = chunk_idx == 0
        is_last = chunk_idx == len(chunks) - 1

        user_prompt = _build_chunk_prompt(
            context_line, chunk, is_first, is_last,
            max_scripts=SCRIPTS_PER_CHUNK,
            diagram_context=diagram_context,
        )

        try:
            with httpx.Client(timeout=120.0) as client:
                resp = client.post(
                    f"{OPENROUTER_BASE}/chat/completions",
                    headers={
                        "Authorization": f"Bearer {OPENROUTER_KEY}",
                        "Content-Type": "application/json",
                        "HTTP-Referer": "https://edu.fixitinpost.in",
                        "X-Title": "CBSE Video Studio",
                    },
                    json={
                        "model": OPENROUTER_MODEL,
                        "messages": [
                            {"role": "system", "content": SYSTEM_PROMPT},
                            {"role": "user", "content": user_prompt},
                        ],
                        "temperature": 0.7,
                        "max_tokens": 4096,
                    },
                )
                resp.raise_for_status()
                data = resp.json()
                content = data["choices"][0]["message"]["content"]

            scripts = _parse_json_response(content)
            print(f"[LLM Chunk {chunk_idx+1}/{len(chunks)}] Got {len(scripts)} scripts", flush=True)

            if scripts:
                all_scripts.extend(scripts)
            else:
                # Fallback for this chunk
                json_match = re.search(r'\{[\s\S]*"scripts"[\s\S]*\}', content)
                if json_match:
                    try:
                        parsed = json.loads(json_match.group())
                        if "scripts" in parsed:
                            all_scripts.extend(parsed["scripts"])
                    except json.JSONDecodeError:
                        pass

        except Exception as e:
            print(f"[LLM Chunk {chunk_idx+1}] Error: {e}", flush=True)
            continue

    if not all_scripts:
        return _rule_based_fallback(raw_text, pdf_title)

    # Merge scripts from all chunks, deduplicate by title
    seen_titles = set()
    merged = []
    for s in all_scripts:
        title = (s.get("title", "") or "").strip()
        if title and title not in seen_titles:
            seen_titles.add(title)
            merged.append(s)

    # Ensure image_prompts on all segments
    merged = _ensure_image_prompts(merged)

    # Cap at max_scripts
    if len(merged) > max_scripts:
        merged = merged[:max_scripts]

    # Adjust cliffhangers between consecutive scripts
    merged = _fix_adjacent_cliffhangers(merged)

    print(f"[LLM Merge] Total: {len(merged)} unique scripts from {len(chunks)} chunks", flush=True)
    return merged


def _chunk_text(text: str, chunk_size: int, overlap: int) -> list:
    """Split text into overlapping chunks at paragraph boundaries."""
    import re
    # Split on paragraph breaks first
    paragraphs = re.split(r'\n\n+', text)
    paragraphs = [p.strip() for p in paragraphs if p.strip()]

    chunks = []
    current = []
    current_len = 0

    for p in paragraphs:
        p_len = len(p)
        if current_len + p_len > chunk_size and current:
            chunks.append("\n\n".join(current))
            # Keep last few paragraphs for overlap
            overlap_chars = 0
            overlap_paras = []
            for cp in reversed(current):
                overlap_chars += len(cp) + 2
                overlap_paras.insert(0, cp)
                if overlap_chars >= overlap:
                    break
            current = list(overlap_paras)
            current_len = sum(len(cp) + 2 for cp in current)
        current.append(p)
        current_len += p_len + 2

    if current:
        chunks.append("\n\n".join(current))

    return chunks if chunks else [text]


def _build_chunk_prompt(context_line: str, chunk_text: str, is_first: bool,
                        is_last: bool, max_scripts: int, diagram_context: str = "") -> str:
    """Build the user prompt for a single chunk."""
    position_hint = ""
    if is_first and is_last:
        position_hint = "This is the ENTIRE chapter content."
    elif is_first:
        position_hint = "This is the START of the chapter (first chunk)."
    elif is_last:
        position_hint = "This is the END of the chapter (last chunk)."
    else:
        position_hint = "This is the MIDDLE of the chapter (continuation chunk)."

    vision_section = ""
    if diagram_context:
        vision_section = f"""
    TEXTBOOK DIAGRAMS (extracted via Gemma vision AI from the PDF pages):
    {diagram_context}
    
    Use these textbook diagram descriptions to inform your image_prompt fields. When a segment
    references content that has a textbook diagram, reference that diagram's visual elements
    (labels, layout, colors) in the image_prompt so generated images match the NCERT curriculum.
    You can also suggest overlaying the actual extracted diagram image in the video frame.
    """

    instructions = f"""{context_line}Generate {max_scripts} YouTube Shorts scripts from this NCERT textbook chunk.

    {position_hint}
    {vision_section}

    REQUIREMENTS (strict):
    1. EVERY script MUST start with a HOOK (first segment = question or surprising observation — grabs attention in <3 secs)
    2. EVERY script MUST end with a CLIFFHANGER (last segment = teaser for what's next — makes viewer scroll)
    3. 9-12 segments per script (segment = 1-2 sentences, one visual beat)
    4. EVERY segment gets a FULLY SELF-CONTAINED image_prompt (40-60 words, describe ONE coherent scene with labels/colors/layout)
    5. Activities: each step = its own segment (setup → observation → result → explanation)
    6. Titles: 5-12 words, curiosity-driven (question or bold statement)
    7. Mark side-content with [ACTIVITY], [THINK IT OVER], [HINT], [DID YOU KNOW] prefixes

    {'The FIRST script MUST hook viewers into the chapter topic.' if is_first else ''}
    {'The LAST script MUST end with a compelling cliffhanger teasing the NEXT chapter.' if is_last else ''}
    {'Since this is a continuation chunk, start scripts by linking back to previous content before diving in.' if not is_first and not is_last else ''}

    Chunk text:
    ```
    {chunk_text}
    ```

    Output JSON: {{"scripts": [{{"title": "...", "segments": [{{"text": "...", "image_prompt": "..."}}]}}]}} Output {max_scripts} scripts max."""

    return instructions


def _fix_adjacent_cliffhangers(scripts: list) -> list:
    """Ensure each script's cliffhanger naturally leads into the next script's hook."""
    for i in range(len(scripts) - 1):
        current = scripts[i]
        next_ = scripts[i + 1]
        segs = current.get("segments", [])
        if segs:
            # If the last segment doesn't tease the next topic, append a transitional cliffhanger
            last_text = segs[-1].get("text", "")
            next_title = next_.get("title", "")
            if next_title and "next" not in last_text.lower() and "up next" not in last_text.lower():
                segs[-1]["text"] = last_text.rstrip(".!?") + f" — but that's just the beginning. Let's see what happens when we {next_title[:40].lower()}..."
    return scripts


def _ensure_image_prompts(scripts: list) -> list:
    """Post-process scripts to ensure every segment has an image_prompt.
    If the LLM missed it, auto-generate one from the segment text."""
    for script in scripts:
        if "segments" not in script:
            continue
        for seg in script["segments"]:
            if not seg.get("image_prompt") or seg["image_prompt"].strip() == "":
                # Auto-generate a visual prompt from segment text
                text = seg.get("text", "")
                # Extract key nouns/concepts
                import re as _re
                text_clean = _re.sub(r'[^\w\s]', '', text)
                words = text_clean.split()
                # Pick significant words (longer than 4 chars, not common stopwords)
                stopwords = {'this', 'that', 'with', 'from', 'have', 'been', 'will', 'into', 'when', 'what', 'which', 'their', 'there', 'where', 'these', 'those', 'about', 'would', 'could', 'should', 'after', 'before'}
                keywords = [w for w in words if len(w) > 4 and w.lower() not in stopwords][:8]
                keywords_str = ", ".join(keywords) if keywords else "educational concept"

                seg["image_prompt"] = (
                    f"Flat vector educational illustration showing {keywords_str}. "
                    f"Clean white background, vibrant colors, clear labels and diagrams. "
                    f"Designed for CBSE student explainer video, modern flat style."
                )
    return scripts


def _parse_json_response(content: str) -> list:
    """Try to extract scripts list from LLM response JSON."""
    # Strip markdown code fences if present
    content = content.strip()
    if content.startswith("```"):
        content = re.sub(r"^```(?:json)?\s*", "", content)
        content = re.sub(r"\s*```$", "", content)
    content = content.strip()

    try:
        parsed = json.loads(content)
        if isinstance(parsed, dict) and "scripts" in parsed:
            return parsed["scripts"]
        elif isinstance(parsed, list):
            return parsed
    except json.JSONDecodeError:
        pass

    return []


def _pre_clean_text(text: str) -> str:
    """Pre-clean OCR/markup junk before sending to LLM."""
    import re
    # Remove square6 markers (PDF layout artifacts)
    text = re.sub(r'[□▪•●■◆»›]', '', text)
    text = re.sub(r'\bsquare6\b', '', text)
    text = re.sub(r'Activity \d+\.\d+Activity \d+\.\d+(Activity \d+\.\d+)*', 'Activity:', text)
    text = re.sub(r'(Figure|Table) [\d.]+.?', '<figure>', text)
    # Merge mid-word spaces (OCR artifacts like "tawa pan" -> keep, but "reac tion" -> "reaction")
    text = re.sub(r'(\w)\s+(\w)', lambda m: m.group(1)+m.group(2) if len(m.group(1))>2 and len(m.group(2))>2 else m.group(0), text)
    # Remove single-letter isolated chars
    text = re.sub(r'\s+[a-zA-Z]\s+', ' ', text)
    # Collapse multiple spaces/newlines
    text = re.sub(r'\n{3,}', '\n\n', text)
    text = re.sub(r'  +', ' ', text)
    return text.strip()


def _rule_based_fallback(raw_text: str, pdf_title: str = "") -> list:
    """Rule-based segmentation as fallback when LLM unavailable."""
    # Use the existing segmenter from text_extractor
    from pipeline.lib.text_extractor import segment_into_scripts
    return segment_into_scripts(raw_text)


# ---------------------------------------------------------------------------
# Testing
# ---------------------------------------------------------------------------
if __name__ == "__main__":
    import sys
    path = sys.argv[1] if len(sys.argv) > 1 else ""

    if path:
        from pipeline.lib.text_extractor import extract
        result = extract(path)
        print(f"Extracted {len(result['text'])} chars via {result['method']}")
        scripts = clean_and_generate(result["text"], pdf_title=Path(path).stem)
        print(json.dumps({"scripts": scripts}, indent=2, ensure_ascii=False))
    else:
        # Test with sample text
        sample = """Chemical Reactions and Equations CHAPTER 1 Consider the following observations: burning of coal, rusting of iron, cooking of food, digestion of food, respiration. All these processes involve chemical changes. A chemical reaction is a process in which one or more substances are converted into one or more new substances. Chemical equations are used to represent chemical reactions. For example: 2H2 + O2 → 2H2O represents the reaction between hydrogen and oxygen to form water. Reactants are the substances that undergo change. Products are the new substances formed. A balanced chemical equation has the same number of atoms of each element on both sides. The law of conservation of mass states that mass can neither be created nor destroyed in a chemical reaction. Therefore, chemical equations must be balanced."""
        scripts = clean_and_generate(sample, pdf_title="Test Chapter")
        print(json.dumps({"scripts": scripts}, indent=2, ensure_ascii=False))