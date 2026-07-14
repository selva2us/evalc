"""
ai_content.py
=============
Replaces rewriter.py's job for the PDF-free flow: rewriter.py turned
*extracted PDF text* into original teaching content. There's no PDF here,
so this generates each page's content directly from the page's place in
the skeleton (its title, plus its pillar/module/chapter breadcrumb for
context) via the Anthropic API.

Runs after the skeleton has been reviewed and submitted to
/api/documents/syllabus-import/<id> (see api_client.import_syllabus and
pipeline.py's run_ai). Output shape matches exactly what uploader.py
already expects, so the existing upload stage needs no changes:

{
  "title": "...",
  "sections": [
    {"type": "html", "value": "..."},
    {"type": "code", "language": "...", "value": "...", "executable": false, ...},
    {"type": "html", "value": "<Key Points ...>"},
    {"type": "html", "value": "<Quick Recap ...>"}
  ]
}
"""
from __future__ import annotations

import json
import re
from pathlib import Path

from . import demo_content
from .llm_providers import complete
from .prompts import get_prompt

# Prompt text lives in prompts/content_generation_prompt.txt (see
# elluval_pipeline/prompts.py) rather than hardcoded here.
SYSTEM_PROMPT = get_prompt("content_generation_prompt")


def _breadcrumb_context(skeleton: dict):
    """Yield (key, title, breadcrumb_str) for every page in document order,
    where key is a stable zero-padded index used as the output filename -
    same convention extractor.py/rewriter.py used, so uploader.py's
    title-matching logic doesn't need to change."""
    technology = skeleton.get("technology_name", "")
    counter = 0
    for pillar in skeleton["pillars"]:
        for mod in pillar["modules"]:
            for chap in mod["chapters"]:
                for page in chap["pages"]:
                    counter += 1
                    breadcrumb = (
                        f"Technology: {technology}\n"
                        f"Pillar: {pillar['title']}\n"
                        f"Module: {mod['title']}\n"
                        f"Chapter: {chap['title']}\n"
                        f"Page: {page['title']}"
                    )
                    yield f"{counter:04d}", page["title"], breadcrumb


class ContentGenerator:
    def __init__(self, cfg, logger, model: str | None = None):
        # Demo Mode: skip the provider call entirely and serve
        # deterministic mock page content. Reverts to the real call
        # automatically once a real API key is configured for the active
        # provider.
        self.demo_mode = getattr(cfg, "is_demo_mode", False)
        self.cfg = cfg
        if self.demo_mode:
            logger.info(
                "Demo Mode active (no %s credentials configured) - "
                "page content will be sample/mock content.", cfg.provider,
            )
        self.model = model or getattr(cfg, "content_model", None)
        self.logger = logger

    def _call(self, title: str, breadcrumb: str) -> dict:
        if self.demo_mode:
            return demo_content.generate_demo_page_content(title, breadcrumb)

        text = complete(
            self.cfg.provider,
            self.cfg.active_api_key,
            self.model,
            system=SYSTEM_PROMPT,
            user=breadcrumb,
            max_tokens=3000,
        )
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

    def generate_page(self, title: str, breadcrumb: str) -> dict:
        generated = self._call(title, breadcrumb)

        sections = [{"type": "html", "value": generated.get("intro_html", "")}]
        if generated.get("explanation_html"):
            sections.append({"type": "html", "value": generated["explanation_html"]})
        if generated.get("code"):
            sections.append({
                "type": "code",
                "language": generated["code"].get("language", "text"),
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

        return {"title": title, "sections": sections}


def generate_all_content(skeleton: dict, work_dir: Path, cfg, logger, state=None) -> list[Path]:
    out_dir = work_dir / "rewritten"
    out_dir.mkdir(parents=True, exist_ok=True)
    generator = ContentGenerator(cfg, logger)

    written = []
    pages = list(_breadcrumb_context(skeleton))
    logger.info("Generating content for %d pages", len(pages))

    for key, title, breadcrumb in pages:
        if state and state.is_done("content", key):
            logger.info("Skipping '%s' (already generated)", title)
            written.append(out_dir / f"{key}.json")
            continue

        logger.info("Generating: %s", title)
        try:
            content = generator.generate_page(title, breadcrumb)
        except Exception as e:
            logger.exception("Content generation failed for '%s': %s", title, e)
            if state:
                state.mark("content", key, f"failed:{e}")
            continue

        out_path = out_dir / f"{key}.json"
        out_path.write_text(json.dumps(content, indent=2))
        written.append(out_path)
        if state:
            state.mark("content", key, "done")

    logger.info("Generated content for %d / %d pages -> %s", len(written), len(pages), out_dir)
    return written
