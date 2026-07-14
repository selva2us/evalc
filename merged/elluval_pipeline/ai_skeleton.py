"""
ai_skeleton.py
==============
Replaces skeleton.py / skeletonold.py's job for the new, PDF-free flow:
given only a technology name (plus optional free-text notes), ask Claude
to draft the full Pillar > Module > Chapter > Page curriculum outline,
parse that markdown into the same tree shape the rest of the pipeline
(uploader.py, ai_content.py) already understands, and hand it to the user
for a one-screen review before anything gets submitted anywhere.

Review step: the generated markdown is written to work_dir/skeleton.md
and opened directly in the user's default browser (webbrowser.open on a
file:// URL) so it's readable full-screen. The file is never deleted, so
it's always available to re-open later even after the process exits.
Confirmation to proceed happens back in the terminal, since that's the
only reliable two-way channel this script has.
"""
from __future__ import annotations

import json
import re
import webbrowser
from dataclasses import asdict, dataclass, field
from pathlib import Path

from . import demo_content
from .llm_providers import complete
from .prompts import get_prompt

# ---------------------------------------------------------------------------
# Tree shape - deliberately identical field names to skeleton.py's dataclasses
# (minus PageInventory, which was PDF-only) so uploader.py / ai_content.py /
# any future pdf_builder.py usage can keep working unchanged against
# skeleton["pillars"].
# ---------------------------------------------------------------------------


@dataclass
class SkeletonPage:
    title: str
    order: int


@dataclass
class SkeletonChapter:
    number: int
    title: str
    pages: list[SkeletonPage] = field(default_factory=list)


@dataclass
class SkeletonModule:
    number: int
    title: str
    chapters: list[SkeletonChapter] = field(default_factory=list)


@dataclass
class SkeletonPillar:
    number: int
    title: str
    modules: list[SkeletonModule] = field(default_factory=list)


def pillars_to_dict(pillars: list[SkeletonPillar]) -> list[dict]:
    return [asdict(p) for p in pillars]


# ---------------------------------------------------------------------------
# The prompt. This is the same "curriculum architect" prompt validated
# earlier - it enforces the Pillar/Module/Chapter/Page hierarchy, the
# 8-15 / 4-10 / 5-12 / 5-20 fan-out limits, and the required topic coverage
# (history, internals, security, performance, testing, enterprise usage,
# interview prep, etc.), plus technology-specific sections.
#
# Prompt text lives in prompts/skeleton_prompt.txt (see
# elluval_pipeline/prompts.py) rather than hardcoded here.
# ---------------------------------------------------------------------------

# Parses lines like "# Pillar 3 – Title", "## Module 12 - Title",
# "### Chapter 2 - Title", "Page 4 - Title" -- but detection is keyword-
# based (Pillar/Module/Chapter/Page), not dependent on exact "#"/"##"/"###"
# heading depth, "-" vs "–" vs ":" separators, or "**bold**" wrapping.
# This matters more now that the same prompt goes to multiple providers:
# Claude tends to follow the "#"-heading-depth convention closely, but
# GPT/Gemini sometimes use a colon instead of a dash, bold the whole line,
# or drop the leading "#"s while still writing "Pillar N: Title" -- see
# _clean_markdown_line() below, which normalizes all of that away before
# these regexes ever see the line.
PILLAR_RE = re.compile(r"^Pillar\s+(\d+)\s*[-–—:.]\s*(.+)$", re.IGNORECASE)
MODULE_RE = re.compile(r"^Module\s+(\d+)\s*[-–—:.]\s*(.+)$", re.IGNORECASE)
CHAPTER_RE = re.compile(r"^Chapter\s+(\d+)\s*[-–—:.]\s*(.+)$", re.IGNORECASE)
PAGE_RE = re.compile(r"^Page\s+(\d+)\s*[-–—:.]\s*(.+)$", re.IGNORECASE)


def _clean_markdown_line(line: str) -> str:
    """Strip whatever the model wrapped a heading line in -- leading
    '#'/'##'/'###' marks, a leading bullet ('-', '*', '•'), and '**bold**'
    wrapping -- so PILLAR_RE etc. can match purely on the keyword
    (Pillar/Module/Chapter/Page) regardless of which of those a given
    provider happened to use for this line."""
    line = line.strip()
    line = re.sub(r"^#{1,6}\s*", "", line)
    line = re.sub(r"^[-*•]\s+", "", line)
    line = line.strip()
    if line.startswith("**") and line.endswith("**") and len(line) > 4:
        line = line[2:-2].strip()
    return line.strip("*").strip()


def build_prompt(technology_name: str, notes: str | None = None) -> str:
    notes_block = ""
    if notes:
        notes_block = (
            f"Additional context from the requester (audience, depth, focus "
            f"areas - respect this when shaping the curriculum):\n{notes.strip()}\n"
        )
    return get_prompt("skeleton_prompt", technology_name=technology_name.strip(), notes_block=notes_block)


def call_model(technology_name: str, notes: str | None, cfg, logger) -> str:
    # Demo Mode: no usable API key for the active provider (or DEMO_MODE
    # forced on) -> serve a deterministic mock skeleton instead of failing.
    # Automatically stops happening the moment a real key is configured
    # (see demo_content.resolve_demo_mode / Config.is_demo_mode).
    if getattr(cfg, "is_demo_mode", False):
        logger.info(
            "Demo Mode active (no %s credentials configured) - "
            "generating a sample skeleton for '%s' instead of calling the API",
            cfg.provider, technology_name,
        )
        return demo_content.generate_demo_skeleton_markdown(technology_name, notes)

    model = getattr(cfg, "skeleton_model", None)
    logger.info(
        "Requesting curriculum skeleton for '%s' from %s (%s)",
        technology_name, model, cfg.provider,
    )

    text = complete(
        cfg.provider,
        cfg.active_api_key,
        model,
        user=build_prompt(technology_name, notes),
        max_tokens=8000,
    )

    if text.startswith("```"):
        lines = text.splitlines()
        if lines[0].startswith("```"):
            lines = lines[1:]
        if lines and lines[-1].strip().startswith("```"):
            lines = lines[:-1]
        text = "\n".join(lines).strip()

    if not text:
        raise RuntimeError("Model returned an empty skeleton.")
    return text


def parse_markdown_to_pillars(markdown: str) -> list[SkeletonPillar]:
    """Parse the '# Pillar / ## Module / ### Chapter / Page' markdown into
    the SkeletonPillar tree. Deliberately tolerant: unrecognized lines are
    skipped rather than raising, since a stray blank line or note shouldn't
    blow up an otherwise-good generation."""
    pillars: list[SkeletonPillar] = []
    current_pillar: SkeletonPillar | None = None
    current_module: SkeletonModule | None = None
    current_chapter: SkeletonChapter | None = None

    for raw_line in markdown.splitlines():
        line = _clean_markdown_line(raw_line)
        if not line:
            continue

        m = PILLAR_RE.match(line)
        if m:
            current_pillar = SkeletonPillar(number=int(m.group(1)), title=m.group(2).strip())
            pillars.append(current_pillar)
            current_module = None
            current_chapter = None
            continue

        m = MODULE_RE.match(line)
        if m and current_pillar is not None:
            current_module = SkeletonModule(number=int(m.group(1)), title=m.group(2).strip())
            current_pillar.modules.append(current_module)
            current_chapter = None
            continue

        m = CHAPTER_RE.match(line)
        if m and current_module is not None:
            current_chapter = SkeletonChapter(number=int(m.group(1)), title=m.group(2).strip())
            current_module.chapters.append(current_chapter)
            continue

        m = PAGE_RE.match(line)
        if m and current_chapter is not None:
            current_chapter.pages.append(SkeletonPage(title=m.group(2).strip(), order=int(m.group(1))))
            continue
        # Anything else (stray commentary, blank separators, etc.) is ignored.

    return pillars


def _counts(pillars: list[SkeletonPillar]) -> tuple[int, int, int, int]:
    n_modules = sum(len(p.modules) for p in pillars)
    n_chapters = sum(len(m.chapters) for p in pillars for m in p.modules)
    n_pages = sum(len(c.pages) for p in pillars for m in p.modules for c in m.chapters)
    return len(pillars), n_modules, n_chapters, n_pages


def generate_skeleton(technology_name: str, work_dir: Path, cfg, logger, notes: str | None = None) -> dict:
    """Main entry point for the AI-driven skeleton stage.

    Writes work_dir/skeleton.md (kept permanently for review/reference) and
    work_dir/skeleton.json (the parsed tree), and returns the same
    {"pillars": [...]} shaped dict the rest of the pipeline expects -
    with a "technology_name" key added and no "source_pdf"/"page_inventory"
    keys, since there's no PDF in this flow.
    """
    markdown = call_model(technology_name, notes, cfg, logger)

    # Write the raw model output BEFORE attempting to parse it, so it's
    # always on disk for inspection -- previously this was written only
    # after a successful parse, which meant the exact file the error
    # message below points you to didn't exist yet on a parse failure.
    work_dir.mkdir(parents=True, exist_ok=True)
    md_path = work_dir / "skeleton.md"
    md_path.write_text(markdown)

    pillars = parse_markdown_to_pillars(markdown)

    if not pillars:
        snippet = markdown[:800] + ("..." if len(markdown) > 800 else "")
        raise RuntimeError(
            f"Could not parse any pillars out of the model's response. "
            f"The raw output was written to {md_path} -- open it to see the "
            f"full text. First part of what the model returned:\n\n{snippet}"
        )

    n_pillars, n_modules, n_chapters, n_pages = _counts(pillars)
    logger.info(
        "Parsed skeleton: %d pillars, %d modules, %d chapters, %d pages",
        n_pillars, n_modules, n_chapters, n_pages,
    )

    skeleton = {
        "technology_name": technology_name,
        "pillars": pillars_to_dict(pillars),
    }
    (work_dir / "skeleton.json").write_text(json.dumps(skeleton, indent=2))
    logger.info("Wrote %s and skeleton.json to %s", md_path.name, work_dir)

    return skeleton


# ---------------------------------------------------------------------------
# Review step
# ---------------------------------------------------------------------------

def open_for_review(md_path: Path, logger) -> None:
    """Open the generated skeleton.md in the default browser, one screen,
    so it can be read top-to-bottom before anything is submitted."""
    url = md_path.resolve().as_uri()
    logger.info("Opening %s for review: %s", md_path.name, url)
    opened = webbrowser.open(url)
    if not opened:
        logger.warning(
            "Could not auto-open a browser in this environment. "
            "Open this file manually to review it: %s", md_path.resolve(),
        )


def confirm_with_user(prompt: str = "\nReviewed the skeleton? Type 'ok' to submit, anything else to abort: ") -> bool:
    answer = input(prompt).strip().lower()
    return answer in {"ok", "y", "yes"}
