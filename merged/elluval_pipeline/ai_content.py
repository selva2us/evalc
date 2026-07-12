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

from anthropic import Anthropic

from . import demo_content

SYSTEM_PROMPT = """You are an instructional designer writing original teaching \
material for a structured online course. You will be given the full \
breadcrumb (Pillar > Module > Chapter > Page) for one page, plus the \
overall technology the course is about. Write complete, original content \
for that page: assume no other source text exists, so explain the concept \
thoroughly and accurately from your own knowledge. Keep the explanation \
focused on exactly what the page title promises, consistent with its place \
in the surrounding hierarchy (don't repeat what a sibling page would cover). \
Use clear, plain English suitable for a motivated learner moving from \
beginner toward professional competency. Add: a short intro, a clear \
explanation, a bulleted "Key Points" summary, and if relevant a short \
"Quick Recap" for memorization. If the page is the kind of topic that \
benefits from a code example (syntax, APIs, patterns, configuration, \
commands), include ONE original, runnable-looking example illustrating it. \
If the page is conceptual/architectural/historical and a code example would \
be artificial, set "code" to null rather than forcing one in. Respond ONLY \
with strict JSON, no markdown fences, no commentary, in this exact shape:
{
  "intro_html": "<p>...</p>",
  "explanation_html": "<div>...</div>",
  "code": {"language": "...", "value": "..."} or null,
  "key_points": ["...", "..."],
  "quick_recap_html": "<div>...</div>" or null
}
"""


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
        # Demo Mode: skip the Anthropic client entirely and serve
        # deterministic mock page content. Reverts to the real client
        # automatically once a real ANTHROPIC_API_KEY is configured.
        self.demo_mode = getattr(cfg, "is_demo_mode", False)
        if self.demo_mode:
            self.client = None
            logger.info(
                "Demo Mode active (no Anthropic credentials configured) - "
                "page content will be sample/mock content."
            )
        else:
            if not cfg.anthropic_api_key:
                raise RuntimeError("ANTHROPIC_API_KEY is required for the content generation stage.")
            self.client = Anthropic(api_key=cfg.anthropic_api_key)
        self.model = model or getattr(cfg, "content_model", None) or "claude-sonnet-4-6"
        self.logger = logger

    def _call(self, title: str, breadcrumb: str) -> dict:
        if self.demo_mode:
            return demo_content.generate_demo_page_content(title, breadcrumb)

        resp = self.client.messages.create(
            model=self.model,
            max_tokens=2000,
            system=SYSTEM_PROMPT,
            messages=[{"role": "user", "content": breadcrumb}],
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
