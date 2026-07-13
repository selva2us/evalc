"""
rewriter.py
===========
Stage 3: turn each extracted block (raw_text + tables + images) into new,
original teaching content adapted for Indian students — this is the step
that replaces "copy the tutorial text into our platform" with "write our
own explanation of the same concept."

Important: the prompt below explicitly asks the model to explain the
underlying concept in its own words and its own examples rather than
lightly reword the source. Treat the extracted raw_text as a *reference
for which concept/code-sample this page covers*, not as text to paraphrase
line-by-line — close paraphrase of someone else's material is still their
material. If you plan to reuse this on other source PDFs, keep that
constraint in the prompt.

Output shape matches what upload_content.rb / migrate_content.rb expect:
{
  "title": "...",
  "sections": [
    {"type": "html", "value": "..."},
    {"type": "code", "language": "html", "value": "...", "executable": false, ...},
    {"type": "image", "value": "<local path>", "caption": "...", "size": "original", "align": "center"}
  ]
}
"""
from __future__ import annotations

import json
import re
from pathlib import Path

from anthropic import Anthropic

from .prompts import get_prompt

# Prompt text lives in prompts/legacy_rewriter_prompt.txt (see
# elluval_pipeline/prompts.py) rather than hardcoded here.
SYSTEM_PROMPT = get_prompt("legacy_rewriter_prompt")


class Rewriter:
    def __init__(self, cfg, logger, model: str = "claude-sonnet-4-6"):
        if not cfg.anthropic_api_key:
            raise RuntimeError("ANTHROPIC_API_KEY is required for the rewrite stage.")
        self.client = Anthropic(api_key=cfg.anthropic_api_key)
        self.model = model
        self.logger = logger

    def _call(self, title: str, raw_text: str, tables: list) -> dict:
        table_note = ""
        if tables:
            table_note = f"\nThe reference page also contains {len(tables)} table(s); " \
                         "if relevant, represent the same information as an HTML <table> " \
                         "in explanation_html, redesigned in your own words."

        user_msg = (
            f"TOPIC: {title}\n\n"
            f"REFERENCE NOTES (for context only, do not paraphrase):\n{raw_text[:6000]}"
            f"{table_note}"
        )
        resp = self.client.messages.create(
            model=self.model,
            max_tokens=2000,
            system=SYSTEM_PROMPT,
            messages=[{"role": "user", "content": user_msg}],
        )
        text = "".join(block.text for block in resp.content if block.type == "text")
        text = re.sub(r"^```json|```$", "", text.strip(), flags=re.MULTILINE).strip()
        try:
            return json.loads(text)
        except json.JSONDecodeError:
            self.logger.error("Model did not return valid JSON for '%s'; storing raw text as fallback", title)
            return {
                "intro_html": f"<p>{title}</p>",
                "explanation_html": f"<div>{text}</div>",
                "code": None,
                "key_points": [],
                "quick_recap_html": None,
            }

    def rewrite_page(self, extracted: dict) -> dict:
        title = extracted["title"]
        generated = self._call(title, extracted.get("raw_text", ""), extracted.get("tables", []))

        sections = [{"type": "html", "value": generated.get("intro_html", "")}]
        if generated.get("explanation_html"):
            sections.append({"type": "html", "value": generated["explanation_html"]})
        if generated.get("code"):
            sections.append({
                "type": "code",
                "language": generated["code"].get("language", "html"),
                "value": generated["code"].get("value", ""),
                "executable": False,
                "expectedOutput": None,
                "sourceFiles": None,
                "matchMode": None,
            })
        if generated.get("key_points"):
            items = "".join(f"<li>{kp}</li>" for kp in generated["key_points"])
            sections.append({"type": "html", "value": f"<div><strong>Key Points</strong><ul>{items}</ul></div>"})
        if generated.get("quick_recap_html"):
            sections.append({"type": "html", "value": generated["quick_recap_html"]})

        for img_path in extracted.get("images", []):
            sections.append({"type": "image", "value": img_path, "caption": "", "size": "original", "align": "center"})

        return {"title": title, "sections": sections}


def rewrite_all(extracted_dir: Path, out_dir: Path, cfg, logger, state=None) -> list[Path]:
    out_dir.mkdir(parents=True, exist_ok=True)
    rewriter = Rewriter(cfg, logger)
    written = []
    for f in sorted(extracted_dir.glob("*.json")):
        key = f.stem
        if state and state.is_done("rewrite", key):
            logger.info("Skipping '%s' (already rewritten)", key)
            written.append(out_dir / f"{key}.json")
            continue
        extracted = json.loads(f.read_text())
        logger.info("Rewriting: %s", extracted["title"])
        try:
            rewritten = rewriter.rewrite_page(extracted)
        except Exception as e:
            logger.exception("Rewrite failed for %s: %s", extracted["title"], e)
            if state:
                state.mark("rewrite", key, f"failed:{e}")
            continue
        out_path = out_dir / f"{key}.json"
        out_path.write_text(json.dumps(rewritten, indent=2))
        written.append(out_path)
        if state:
            state.mark("rewrite", key, "done")
    logger.info("Rewrote %d pages to %s", len(written), out_dir)
    return written
