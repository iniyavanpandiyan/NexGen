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

SYSTEM_PROMPT = """You are an expert short-form video producer and mathematical communicator.
Your task is to adapt the provided textbook chapter into a cohesive, multi-part
series of highly engaging Reel/TikTok scripts.

[SCRIPT REQUIREMENTS]
- Duration: Each script must be 150 to 300 words long
  (1 to 2 minutes of screen time).
- Formatting: EVERY line and paragraph must be kept under 10 words
  to fit video teleprompters perfectly.
- Visual Polish: Do NOT use any emojis, bolding, or markdown text
  formatting in the final output.
- Structure: Each script must contain an aggressive visual/audio Hook,
  a Core Lesson, a Cliffhanger, and a Call to Action (CTA).

[SERIES COHESION]
- Ensure the scripts form a continuous narrative arc across the chapter.
- The end of one script must seamlessly setup the hook of the next script.

[TONE & CHARACTER VARIATIONS]
Generate 3 distinct script variations for each concept using these personas:

Variation 1: The "Street-Smart" Storyteller
- Tone: High-energy, casual, relatable, deeply practical.
- Framing: Explains the math using real-world hustles, daily life,
  or unexpected human history.

Variation 2: The Cosmic Philosopher
- Tone: Thoughtful, mind-bending, existential, cinematic.
- Framing: Focuses on how this specific math reveals the hidden,
  invisible geometry of the universe.

Variation 3: The Secret Agent / Tech Thriller
- Tone: Urgent, mysterious, high-stakes, intense.
- Framing: Frames the math concept as a "classified shortcut" or code
  used to hack systems or beat the odds.

[OUTPUT FORMAT - JSON]
Output valid JSON (no markdown fences, no extra text) like this:
{
  "scripts": [
    {
      "title": "The Casino Secret",
      "variation": 3,
      "variation_name": "Secret Agent / Tech Thriller",
      "segments": [
        {"text": "Most people lose.", "image_prompt": "..."},
        {"text": "The math is rigged.", "image_prompt": "..."},
        {"text": "But you can win.", "image_prompt": "..."},
        {"text": "Here is the secret.", "image_prompt": "..."}
      ],
      "hook": "Most people lose. The math is rigged. But you can win. Here is the secret.",
      "core_lesson": "Look at expected value. It predicts long-term outcomes. Multiply the probability by the payout. Subtract the losing probability. If the number is positive, play. If it is negative, walk away. Casinos hide this simple formula. It turns luck into pure logic.",
      "cliffhanger": "But there is a fatal flaw. One mistake ruins everything. It wipes out your entire bankroll.",
      "cta": "Follow to see the flaw tomorrow."
    }
  ]
}

Image prompt rules (for "image_prompt" in each segment):
- "Flat vector educational illustration. Clean white background, vibrant colors, modern style."
- Include ALL of: objects, people, spatial layout, labels, colors, arrows, emotion.
- Describe ONE coherent scene, fully self-contained.
- 30-60 words per prompt.

IMPORTANT:
- Every line under 10 words for teleprompter readability.
- No emojis, no bold, no markdown.
- Scripts form a continuous narrative across the chapter.
- 150-300 words per script total (spread across segments)."""


def clean_and_generate(raw_text: str, pdf_title: str = "", pdf_class: str = "",
                        pdf_subject: str = "", max_scripts: int = 96,
                       diagram_context: str = "", llm_backend: str = "openrouter") -> list:
    """Clean PDF text and generate structured scripts via LLM.

    Args:
        llm_backend: "openrouter" (default) or "local" (local Gemma via llama.cpp)
    """
    if not raw_text or len(raw_text.strip()) < 50:
        return []

    raw_text = _pre_clean_text(raw_text)

    if llm_backend == "local":
        return _generate_via_local_gemma(raw_text, pdf_title, pdf_class, pdf_subject,
                                          max_scripts, diagram_context)

    if not OPENROUTER_KEY:
        return _rule_based_fallback(raw_text, pdf_title)

    CHUNK_SIZE = 24000
    CHUNK_OVERLAP = 2000
    SCRIPTS_PER_CHUNK = 6

    chunks = _chunk_text(raw_text, CHUNK_SIZE, CHUNK_OVERLAP)
    max_chunks = min(len(chunks), 16)
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
                        "X-Title": "NexGen",
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

    seen_titles = set()
    merged = []
    for s in all_scripts:
        title = (s.get("title", "") or "").strip()
        if title and title not in seen_titles:
            seen_titles.add(title)
            merged.append(s)

    merged = _ensure_image_prompts(merged)

    if len(merged) > max_scripts:
        merged = merged[:max_scripts]

    merged = _fix_adjacent_cliffhangers(merged)

    print(f"[LLM Merge] Total: {len(merged)} unique scripts from {len(chunks)} chunks", flush=True)
    return merged


def _generate_via_local_gemma(raw_text: str, pdf_title: str = "", pdf_class: str = "",
                               pdf_subject: str = "", max_scripts: int = 96,
                               diagram_context: str = "") -> list:
    """Generate scripts using local Gemma (llama.cpp) instead of OpenRouter."""
    LLAMA_HOST = os.environ.get("LLAMA_HOST", "http://127.0.0.1:8082")
    import requests

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

    user_prompt = f"""{context_line}Generate up to {max_scripts} Reel/TikTok scripts from this NCERT textbook chapter.

This is the ENTIRE chapter content in one chunk.

{diagram_context}

Chunk text:
```
{raw_text[:64000]}
```

Output JSON: {{"scripts": [{{"title": "...", "variation": 1, "variation_name": "...", "segments": [{{"text": "...", "image_prompt": "..."}}], "hook": "...", "core_lesson": "...", "cliffhanger": "...", "cta": "..."}}]}} Output {max_scripts} scripts max."""

    payload = {
        "messages": [
            {"role": "system", "content": SYSTEM_PROMPT},
            {"role": "user", "content": [{"type": "text", "text": user_prompt}]},
        ],
        "max_tokens": 8192,
        "temperature": 0.7,
    }

    try:
        resp = requests.post(f"{LLAMA_HOST}/v1/chat/completions", json=payload, timeout=300)
        resp.raise_for_status()
        content = resp.json()["choices"][0]["message"]["content"]
        scripts = _parse_json_response(content)
        if scripts:
            scripts = _ensure_image_prompts(scripts)
            if len(scripts) > max_scripts:
                scripts = scripts[:max_scripts]
            print(f"[Local Gemma] Got {len(scripts)} scripts", flush=True)
            return scripts
    except Exception as e:
        print(f"[Local Gemma] Error: {e}, falling back to rule-based", flush=True)

    return _rule_based_fallback(raw_text, pdf_title)


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

    instructions = f"""{context_line}Generate {max_scripts} Reel/TikTok scripts from this NCERT textbook chunk.

{position_hint}
{vision_section}

SCRIPT STRUCTURE for every script:
- Hook: 4-8 very short lines (<10 words each), aggressive grabber
- Core Lesson: 10-20 short lines explaining the concept
- Cliffhanger: 3-5 short lines teasing what comes next
- CTA: 2-3 short lines prompting follow/subscribe

TONE VARIATIONS to use (one variation per script output):
1 = Street-Smart Storyteller (high-energy, casual, real-world hustles)
2 = Cosmic Philosopher (thoughtful, cinematic, hidden geometry of universe)
3 = Secret Agent / Tech Thriller (urgent, high-stakes, classified code)

{'The FIRST script MUST hook viewers into the chapter topic with Variation 1.' if is_first else ''}
{'The LAST script MUST end with a compelling cliffhanger teasing the NEXT chapter.' if is_last else ''}

Chunk text:
```
{chunk_text}
```

Output JSON: {{"scripts": [{{"title": "...", "variation": 1, "variation_name": "...", "segments": [{{"text": "...", "image_prompt": "..."}}], "hook": "...", "core_lesson": "...", "cliffhanger": "...", "cta": "..."}}]}} Output {max_scripts} scripts max."""

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