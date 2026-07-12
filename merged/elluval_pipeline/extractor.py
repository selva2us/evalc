"""
extractor.py
============
Stage 2: given the skeleton produced by skeleton.py, slice the PDF body
into one raw-content block per page/section, and extract:
  - the text belonging to that block
  - any tables detected in it
  - any content-sized images (logos/watermarks filtered out by size)

This is the Python equivalent of extract_content.rb's heading-search +
code-block heuristic, generalized to also capture tables (which the Ruby
version didn't handle) since the skeleton now flags which pages have them.

Output: one JSON file per page under work/extracted/<page_order>.json:
{
  "title": "...",
  "raw_text": "...",
  "tables": [[["h1","h2"], ["a","b"]], ...],
  "images": ["work/extracted_images/img-12-0.png", ...]
}
"""
from __future__ import annotations

import json
import re
from pathlib import Path

import pdfplumber

MIN_IMG_WIDTH = 400
MIN_IMG_HEIGHT = 100
NOISE_RE = re.compile(r"\A\s*(HTML|\d+)\s*\Z")
HEADING_PREFIX_RE = re.compile(r"\A(Chapter\s+\d+:|Section\s+[\d.]+:)\s*", re.IGNORECASE)


LIGATURE_MAP = str.maketrans({
    "\ufb00": "ff", "\ufb01": "fi", "\ufb02": "fl", "\ufb03": "ffi", "\ufb04": "ffl",
})


def normalize(s: str) -> str:
    """Lowercase/whitespace-normalize AND strip a leading 'Chapter N:' or
    'Section N.M:' label. Skeleton titles are already stripped of these
    (skeleton.py records the clean title), but the PDF body text still
    prints headings as e.g. 'Section 1.1: Hello World' - without this,
    heading search would never find a match for that style of source.
    Also decomposes typographic ligatures (ﬁ/ﬂ/ﬀ) some PDF fonts emit,
    which otherwise silently break exact-text matching."""
    s = s.translate(LIGATURE_MAP)
    s = HEADING_PREFIX_RE.sub("", s)
    return re.sub(r"\s+", " ", s).strip().lower()


def _flatten_pages(skeleton: dict) -> list[dict]:
    """Flat, ordered list of {id-ish key, title} across the whole skeleton,
    in document order, each carrying a unique key we use as filename."""
    flat = []
    counter = 0
    for pillar in skeleton["pillars"]:
        for mod in pillar["modules"]:
            for chap in mod["chapters"]:
                for page in chap["pages"]:
                    counter += 1
                    flat.append({"key": f"{counter:04d}", "title": page["title"]})
    return flat


def _body_lines_with_page(pdf_path: str, body_start_page: int, total_pages: int) -> list[dict]:
    lines = []
    with pdfplumber.open(pdf_path) as pdf:
        for n in range(body_start_page, total_pages + 1):
            text = pdf.pages[n - 1].extract_text() or ""
            for line in text.splitlines():
                lines.append({"page": n, "line": line.replace("\f", "")})
    return lines


def _dump_images(pdf_path: str, out_dir: Path, logger) -> dict[int, list[str]]:
    """Use PyMuPDF to dump every embedded image, filter by min size, group
    by the PDF page it appears on."""
    import fitz

    out_dir.mkdir(parents=True, exist_ok=True)
    images_by_page: dict[int, list[str]] = {}
    doc = fitz.open(pdf_path)
    kept = 0
    for page_index in range(len(doc)):
        page = doc[page_index]
        for img_idx, img in enumerate(page.get_images(full=True)):
            xref = img[0]
            try:
                base = doc.extract_image(xref)
            except Exception:
                continue
            width, height = base.get("width", 0), base.get("height", 0)
            if width < MIN_IMG_WIDTH or height < MIN_IMG_HEIGHT:
                continue
            ext = base.get("ext", "png")
            fname = out_dir / f"img-{page_index + 1}-{img_idx}.{ext}"
            fname.write_bytes(base["image"])
            images_by_page.setdefault(page_index + 1, []).append(str(fname))
            kept += 1
    doc.close()
    logger.info("Kept %d content-sized images across %d pages (logos filtered out)",
                kept, len(images_by_page))
    return images_by_page


def extract_all(skeleton: dict, work_dir: Path, logger) -> list[Path]:
    pdf_path = skeleton["source_pdf"]
    body_start_page = skeleton["body_start_page"]
    total_pages = skeleton["total_pdf_pages"]

    flat_pages = _flatten_pages(skeleton)
    if not flat_pages:
        raise RuntimeError("Skeleton has no pages to extract.")
    logger.info("Extracting content for %d pages", len(flat_pages))

    body_lines = _body_lines_with_page(pdf_path, body_start_page, total_pages)
    images_by_page = _dump_images(pdf_path, work_dir / "extracted_images", logger)

    # Locate each page's heading, monotonically, same approach as the Ruby version.
    # Try a single-line exact match first; if that fails, fall back to joining
    # the current line with the next one, since long headings sometimes wrap
    # across two lines in the body text (the TOC entry itself never wraps).
    cursor = 0
    blocks = []
    for pg in flat_pages:
        target = normalize(pg["title"])
        idx, consumed = None, 1
        for i in range(cursor, len(body_lines)):
            if normalize(body_lines[i]["line"]) == target:
                idx, consumed = i, 1
                break
            if i + 1 < len(body_lines):
                joined = normalize(body_lines[i]["line"] + " " + body_lines[i + 1]["line"])
                if joined == target:
                    idx, consumed = i, 2
                    break
        if idx is None:
            logger.warning('Could not locate heading for "%s" - skipping', pg["title"])
            continue
        blocks.append({"key": pg["key"], "title": pg["title"], "start": idx + consumed,
                        "heading_page": body_lines[idx]["page"]})
        cursor = idx + consumed

    for i, b in enumerate(blocks):
        b["end"] = blocks[i + 1]["start"] - 1 if i + 1 < len(blocks) else len(body_lines)

    logger.info("Matched %d / %d page headings in the PDF body", len(blocks), len(flat_pages))

    out_dir = work_dir / "extracted"
    out_dir.mkdir(parents=True, exist_ok=True)
    written = []

    with pdfplumber.open(pdf_path) as pdf:
        for b in blocks:
            lines = [body_lines[i]["line"] for i in range(b["start"], b["end"])]
            lines = [l for l in lines if not NOISE_RE.match(l)]
            raw_text = "\n".join(lines).strip()

            pages_spanned = sorted({body_lines[i]["page"] for i in range(b["start"], b["end"])} | {b["heading_page"]})
            tables = []
            for pn in pages_spanned:
                for t in (pdf.pages[pn - 1].extract_tables() or []):
                    tables.append(t)
            images = [img for pn in pages_spanned for img in images_by_page.get(pn, [])]

            payload = {"title": b["title"], "raw_text": raw_text, "tables": tables, "images": images}
            out_path = out_dir / f"{b['key']}.json"
            out_path.write_text(json.dumps(payload, indent=2))
            written.append(out_path)

    logger.info("Wrote %d extracted content files to %s", len(written), out_dir)
    return written
