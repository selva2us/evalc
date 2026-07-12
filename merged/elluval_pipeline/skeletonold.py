"""
skeleton.py
===========
Stage 1 of the pipeline: analyze the reference PDF and produce a
structured skeleton describing its educational shape — pillars, modules,
chapters, pages/sections — plus, per page, a rough inventory of what's on
it (headings, bullet lists, tables, callouts, images) so later stages know
what kind of content to regenerate and where visuals belong.

This replaces pdf_to_skeleton.rb. The Ruby version only parsed the
dot-leader table of contents into a Pillar > Module > Chapter > Page
markdown outline. This version keeps that same grouping logic (so
existing subject trees built from it stay compatible) but additionally
walks the actual body pages with pdfplumber + PyMuPDF to detect:
  - headings/subheadings (font-size based)
  - bullet/numbered lists
  - tables
  - callout boxes (heuristic: bordered/shaded rects with text inside)
  - images/diagrams (extracted separately in extractor.py; here we just
    record presence + bounding boxes so extractor.py doesn't redo layout
    analysis)

Output: work/skeleton.json - the full hierarchical skeleton, and
work/skeleton.md - the same human-readable outline the Ruby script produced.
"""
from __future__ import annotations

import json
import re
from dataclasses import asdict, dataclass, field
from pathlib import Path

import fitz  # PyMuPDF
import pdfplumber

# Two TOC conventions are supported here:
#   1. "1.   Some Title .......... 12"           (module)
#      "     Sub Title ............ 3"           (page, plain indent)
#   2. "Chapter 1: Some Title .......... 12"      (module, GoalKicker/
#      "     Section 1.1: Sub Title ..... 3"      "Notes for Professionals"-style books)
# MODULE_RE/PAGE_RE try both; the numeric prefix or "Chapter"/"Section" label
# is stripped so the resulting title is clean either way.
MODULE_RE = re.compile(
    r"\A(?:\d+\.\s+|Chapter\s+\d+:\s+)(?P<title>.+?)\s*\.{2,}\s*\d+\s*\Z", re.IGNORECASE
)
PAGE_RE = re.compile(
    r"\A\s+(?:Section\s+[\d.]+:\s+)?(?P<title>.+?)\s*\.{2,}\s*\d+\s*\Z", re.IGNORECASE
)
FRONT_MATTER_RE = re.compile(
    r"\A(About( the Tutorial)?|Audience|Prerequisites|Copyright.*Disclaimer|Table of Contents)\Z",
    re.IGNORECASE,
)
BULLET_RE = re.compile(r"^\s*([•\-\*▪]|[0-9]+[.)])\s+")


@dataclass
class PageInventory:
    """What's physically present on one PDF page (post body-start)."""
    pdf_page_number: int
    heading_candidates: list[str] = field(default_factory=list)
    bullet_lines: int = 0
    has_table: bool = False
    table_count: int = 0
    image_count: int = 0
    image_bboxes: list[tuple] = field(default_factory=list)
    callout_like: bool = False  # bordered/shaded rect with text - heuristic


@dataclass
class SkeletonPage:
    title: str
    order: int
    inventory: PageInventory | None = None


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


def clean_title(title: str) -> str:
    return re.sub(r"\s+", " ", title).strip()


def toc_page(text: str) -> bool:
    lines = [l for l in text.splitlines() if l.strip()]
    if not lines:
        return False
    dot_lines = sum(1 for l in lines if re.search(r"\.{2,}\s*\d+\s*$", l))
    return (dot_lines / len(lines)) > 0.4


def _dedent_common(text: str) -> str:
    """pdfplumber's layout=True mode pads every line relative to the
    left-most content found anywhere on the page, so even "column 0"
    module lines can carry a constant left margin of spaces. Strip the
    minimum common indentation across all non-blank lines so the
    MODULE_RE/PAGE_RE regexes (which expect module lines to start at
    column 0) work the same way they would against `pdftotext -layout`
    output for a real-world PDF."""
    lines = text.splitlines()
    indents = [len(l) - len(l.lstrip(" ")) for l in lines if l.strip()]
    if not indents:
        return text
    min_indent = min(indents)
    if min_indent == 0:
        return text
    return "\n".join(l[min_indent:] if l.strip() else l for l in lines)


def parse_toc_entries(full_text: str) -> list[dict]:
    full_text = _dedent_common(full_text)
    entries = []
    for raw_line in full_text.splitlines():
        line = raw_line.rstrip("\f")
        if not line.strip():
            continue
        m = MODULE_RE.match(line)
        if m:
            entries.append({"level": "module", "title": clean_title(m.group("title"))})
            continue
        m = PAGE_RE.match(line)
        if m:
            title = clean_title(m.group("title"))
            if FRONT_MATTER_RE.match(title):
                continue
            entries.append({"level": "page", "title": title})
    return entries


def build_modules(entries: list[dict]) -> list[dict]:
    modules, current = [], None
    for e in entries:
        if e["level"] == "module":
            current = {"title": e["title"], "pages": []}
            modules.append(current)
        else:
            if current is None:
                current = {"title": "Introduction", "pages": []}
                modules.append(current)
            current["pages"].append(e["title"])
    return [m for m in modules if m["pages"]]


def chapters_for(pages: list[str], per_chapter: int) -> list[dict]:
    return [
        {"number": i + 1, "pages": pages[i * per_chapter:(i + 1) * per_chapter]}
        for i in range((len(pages) + per_chapter - 1) // per_chapter)
    ]


def build_hierarchy(modules: list[dict], pages_per_chapter: int, modules_per_pillar: int) -> list[SkeletonPillar]:
    pillars = []
    for pillar_idx, mod_group in enumerate(
        [modules[i:i + modules_per_pillar] for i in range(0, len(modules), modules_per_pillar)], start=1
    ):
        label = mod_group[0]["title"] if len(mod_group) == 1 else f'{mod_group[0]["title"]} to {mod_group[-1]["title"]}'
        pillar = SkeletonPillar(number=pillar_idx, title=label)
        for idx_in_pillar, mod in enumerate(mod_group, start=1):
            module_number = (pillar_idx - 1) * modules_per_pillar + idx_in_pillar
            skel_mod = SkeletonModule(number=module_number, title=mod["title"])
            for chap in chapters_for(mod["pages"], pages_per_chapter):
                skel_chap = SkeletonChapter(number=chap["number"], title=f'{mod["title"]} (Part {chap["number"]})')
                for i, page_title in enumerate(chap["pages"], start=1):
                    skel_chap.pages.append(SkeletonPage(title=page_title, order=i))
                skel_mod.chapters.append(skel_chap)
            pillar.modules.append(skel_mod)
        pillars.append(pillar)
    return pillars


# ---------------------------------------------------------------------------
# Per-page visual inventory (headings/lists/tables/images/callouts)
# ---------------------------------------------------------------------------

def _heading_candidates(page: "fitz.Page", body_font_size: float) -> list[str]:
    headings = []
    text_dict = page.get_text("dict")
    for block in text_dict.get("blocks", []):
        for line in block.get("lines", []):
            for span in line.get("spans", []):
                size = span.get("size", 0)
                text = span.get("text", "").strip()
                if text and size >= body_font_size + 2:
                    headings.append(text)
    return headings


def _estimate_body_font_size(doc: "fitz.Document", sample_pages: int = 5) -> float:
    sizes = []
    for page in doc[:sample_pages]:
        for block in page.get_text("dict").get("blocks", []):
            for line in block.get("lines", []):
                for span in line.get("spans", []):
                    if span.get("text", "").strip():
                        sizes.append(span["size"])
    if not sizes:
        return 10.0
    sizes.sort()
    return sizes[len(sizes) // 2]  # median


def analyze_page_inventory(pdf_path: str, body_start_page: int, total_pages: int) -> dict[int, PageInventory]:
    inventory: dict[int, PageInventory] = {}
    doc = fitz.open(pdf_path)
    body_font_size = _estimate_body_font_size(doc)

    with pdfplumber.open(pdf_path) as pdf:
        for n in range(body_start_page, total_pages + 1):
            fitz_page = doc[n - 1]
            plumber_page = pdf.pages[n - 1]

            headings = _heading_candidates(fitz_page, body_font_size)
            text = plumber_page.extract_text() or ""
            bullet_lines = sum(1 for l in text.splitlines() if BULLET_RE.match(l))
            tables = plumber_page.extract_tables() or []
            images = fitz_page.get_images(full=True)
            image_bboxes = [tuple(fitz_page.get_image_bbox(img)) for img in images] if images else []

            # Heuristic callout detector: rectangles with a fill/stroke that
            # aren't the full page (i.e. a "box" drawn around a tip/note).
            callout_like = False
            for rect in plumber_page.rects:
                w, h = rect["width"], rect["height"]
                if 0 < w < plumber_page.width * 0.95 and 0 < h < plumber_page.height * 0.4 and h > 20:
                    callout_like = True
                    break

            inventory[n] = PageInventory(
                pdf_page_number=n,
                heading_candidates=headings[:5],
                bullet_lines=bullet_lines,
                has_table=bool(tables),
                table_count=len(tables),
                image_count=len(images),
                image_bboxes=image_bboxes,
                callout_like=callout_like,
            )
    doc.close()
    return inventory


# ---------------------------------------------------------------------------
# Rendering the markdown outline (same shape as the Ruby output)
# ---------------------------------------------------------------------------

def render_markdown(pillars: list[SkeletonPillar]) -> str:
    lines = []
    for pillar in pillars:
        lines.append(f"# Pillar {pillar.number} - {pillar.title}")
        for mod in pillar.modules:
            lines.append(f"## Module {mod.number} - {mod.title}")
            for chap in mod.chapters:
                lines.append(f"### Chapter {chap.number} - {chap.title}")
                for i, page in enumerate(chap.pages, start=1):
                    lines.append(f"Page {i} - {page.title}")
                lines.append("")
    return "\n".join(lines).rstrip() + "\n"


def pillars_to_dict(pillars: list[SkeletonPillar]) -> list[dict]:
    return [asdict(p) for p in pillars]


def generate_skeleton(pdf_path: str, work_dir: Path, cfg, logger) -> dict:
    """Full stage-1 entry point. Returns the skeleton dict and writes it +
    the markdown outline to work_dir."""
    logger.info("Extracting full text for TOC parsing...")
    with pdfplumber.open(pdf_path) as pdf:
        total_pages = len(pdf.pages)
        # layout=True preserves indentation (like `pdftotext -layout`), which
        # the TOC regexes below rely on to tell module lines from page lines.
        toc_texts = [(p.extract_text(layout=True) or "") for p in pdf.pages]
        full_text = "\n".join(_dedent_common(t) for t in toc_texts)

    logger.info("PDF has %d physical pages", total_pages)

    entries = parse_toc_entries(full_text)
    if not entries:
        raise RuntimeError(
            "No dot-leader table-of-contents entries found. This PDF may not "
            "follow the expected 'Title ....... N' TOC format."
        )
    modules = build_modules(entries)
    logger.info("Parsed %d modules from TOC covering %d pages",
                len(modules), sum(len(m["pages"]) for m in modules))

    pillars = build_hierarchy(modules, cfg.pages_per_chapter, cfg.modules_per_pillar)

    body_start_page = next((i + 1 for i, t in enumerate(toc_texts) if not toc_page(t)), 1)
    logger.info("Body content estimated to start at PDF page %d", body_start_page)

    logger.info("Scanning body pages for headings/tables/images/callouts (this can take a while)...")
    inventory = analyze_page_inventory(pdf_path, body_start_page, total_pages)

    skeleton = {
        "source_pdf": str(pdf_path),
        "total_pdf_pages": total_pages,
        "body_start_page": body_start_page,
        "pillars": pillars_to_dict(pillars),
        "page_inventory": {str(k): asdict(v) for k, v in inventory.items()},
    }

    (work_dir / "skeleton.json").write_text(json.dumps(skeleton, indent=2))
    (work_dir / "skeleton.md").write_text(render_markdown(pillars))
    logger.info("Wrote skeleton.json and skeleton.md to %s", work_dir)
    return skeleton
