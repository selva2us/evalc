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

from anthropic import Anthropic

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
# ---------------------------------------------------------------------------

SKELETON_PROMPT_TEMPLATE = """You are a world-class curriculum architect, senior software engineer, technical author, and educational content designer.
Your task is to generate a COMPLETE markdown learning skeleton for the technology provided by the user.
Technology:
{technology_name}
{notes_block}
The output must be a clean and structured markdown document.
The markdown must follow this hierarchy exactly:
# Pillar
## Module
### Chapter
Page
Example:
# Pillar 1 – Foundations
## Module 1 – Introduction
### Chapter 1 – History
Page 1 - Origins
Page 2 - Evolution
Page 3 - Major Milestones
Rules:
1. Generate between 8 and 15 Pillars.
2. Each Pillar should contain 4 to 10 Modules.
3. Each Module should contain 5 to 12 Chapters.
4. Each Chapter should contain 5 to 20 Pages.
5. The structure must progress naturally from beginner to expert level.
The curriculum must include:
- History and evolution
- Core concepts
- Syntax and fundamentals
- Internal architecture
- Runtime behavior
- Memory management
- Design patterns
- Ecosystem and libraries
- Tooling
- Security
- Performance
- Debugging
- Testing
- Deployment
- Best practices
- Real-world applications
- Enterprise usage
- Advanced topics
- Common mistakes
- Interview preparation
- Future roadmap
Technology-specific sections must be included, appropriate to {technology_name}.
Requirements:
- Avoid generic tutorials.
- Do not generate content explanations.
- Generate only the curriculum structure.
- Ensure every topic appears exactly once.
- Avoid duplicate chapters.
- Ensure logical learning progression.
- Prefer industry standards over academic ordering.
- Include internals and architecture wherever applicable.
- Include historical context where relevant.
- Include deprecated technologies if they influenced modern design.
- Include ecosystem tools and alternatives.
- Include production and enterprise usage patterns.
Formatting rules:
- Output must be valid markdown.
- Use only markdown headings and bullet-free page lines.
- Do not include introductory text.
- Do not include conclusions.
- Do not include explanations outside the hierarchy.
- Use title case for all headings.
- Keep naming concise and professional.
Respond with ONLY the markdown document. No preamble, no code fences, no commentary.
"""

# Parses "# Pillar 3 – Title", "## Module 12 - Title", "### Chapter 2 - Title",
# "Page 4 - Title". Both "-" and "–" are accepted since models mix them.
PILLAR_RE = re.compile(r"^#\s*Pillar\s+(\d+)\s*[-–]\s*(.+)$", re.IGNORECASE)
MODULE_RE = re.compile(r"^##\s*Module\s+(\d+)\s*[-–]\s*(.+)$", re.IGNORECASE)
CHAPTER_RE = re.compile(r"^###\s*Chapter\s+(\d+)\s*[-–]\s*(.+)$", re.IGNORECASE)
PAGE_RE = re.compile(r"^Page\s+(\d+)\s*[-–]\s*(.+)$", re.IGNORECASE)


def build_prompt(technology_name: str, notes: str | None = None) -> str:
    notes_block = ""
    if notes:
        notes_block = (
            f"Additional context from the requester (audience, depth, focus "
            f"areas - respect this when shaping the curriculum):\n{notes.strip()}\n"
        )
    return SKELETON_PROMPT_TEMPLATE.format(technology_name=technology_name.strip(), notes_block=notes_block)


def call_model(technology_name: str, notes: str | None, cfg, logger) -> str:
    if not cfg.anthropic_api_key:
        raise RuntimeError("ANTHROPIC_API_KEY is required to generate a skeleton.")

    client = Anthropic(api_key=cfg.anthropic_api_key)
    model = getattr(cfg, "skeleton_model", None) or "claude-sonnet-4-6"
    logger.info("Requesting curriculum skeleton for '%s' from %s", technology_name, model)

    resp = client.messages.create(
        model=model,
        max_tokens=8000,
        messages=[{"role": "user", "content": build_prompt(technology_name, notes)}],
    )
    text = "".join(block.text for block in resp.content if block.type == "text").strip()

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
        line = raw_line.strip()
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
    pillars = parse_markdown_to_pillars(markdown)

    if not pillars:
        raise RuntimeError(
            "Could not parse any pillars out of the model's response. "
            "Check work_dir/skeleton.md (written below) to see the raw output."
        )

    n_pillars, n_modules, n_chapters, n_pages = _counts(pillars)
    logger.info(
        "Parsed skeleton: %d pillars, %d modules, %d chapters, %d pages",
        n_pillars, n_modules, n_chapters, n_pages,
    )

    work_dir.mkdir(parents=True, exist_ok=True)
    md_path = work_dir / "skeleton.md"
    md_path.write_text(markdown)

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
