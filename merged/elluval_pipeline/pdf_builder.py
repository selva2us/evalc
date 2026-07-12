"""
pdf_builder.py
==============
Stage 5: assemble the rewritten, uploaded content back into a single
polished PDF for the subject - preserving the Pillar > Module > Chapter >
Page structure from the skeleton, with headings, body text, code blocks
(monospace boxes), bullet summaries, and images laid out so nothing
overlaps (reportlab's Platypus flow handles pagination/overlap for us;
we just need to size images and wrap code blocks correctly).
"""
from __future__ import annotations

import json
import re
from pathlib import Path

from reportlab.lib import colors
from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import ParagraphStyle, getSampleStyleSheet
from reportlab.lib.units import cm
from reportlab.platypus import (
    Image,
    ListFlowable,
    ListItem,
    PageBreak,
    Paragraph,
    SimpleDocTemplate,
    Spacer,
    Table,
    TableStyle,
)

STYLES = getSampleStyleSheet()
STYLES.add(ParagraphStyle("PillarTitle", fontSize=22, leading=26, spaceAfter=18, textColor=colors.HexColor("#1a2b4c")))
STYLES.add(ParagraphStyle("ModuleTitle", fontSize=17, leading=21, spaceBefore=14, spaceAfter=10, textColor=colors.HexColor("#22406b")))
STYLES.add(ParagraphStyle("ChapterTitle", fontSize=14, leading=18, spaceBefore=10, spaceAfter=8, textColor=colors.HexColor("#2b5a8a")))
STYLES.add(ParagraphStyle("PageTitle", fontSize=12.5, leading=16, spaceBefore=8, spaceAfter=6, textColor=colors.HexColor("#333333")))
STYLES.add(ParagraphStyle("Body", fontSize=10, leading=14, spaceAfter=6))
STYLES.add(ParagraphStyle("CodeBlock", fontName="Courier", fontSize=8.5, leading=11, backColor=colors.HexColor("#f4f4f4"),
                           borderPadding=6, spaceAfter=8))

TAG_STRIP_RE = re.compile(r"<(?!/?(b|i|u|br|super|sub)\b)[^>]*>")


def _html_to_paragraphs(html: str) -> list:
    """Very small HTML->paragraph shim: split on block-level tags, strip
    anything reportlab's Paragraph markup doesn't understand, keep basic
    inline tags (b/i/u/br/super/sub) since Paragraph supports those."""
    if not html:
        return []
    blocks = re.split(r"</(?:div|p|li)>", html)
    paras = []
    for block in blocks:
        text = TAG_STRIP_RE.sub("", block)
        text = text.replace("<br>", "<br/>").strip()
        text = re.sub(r"</?(div|p|li|ul|strong)>", lambda m: "<b>" if "strong" in m.group(0) else "", text)
        text = text.strip()
        if text:
            paras.append(Paragraph(text, STYLES["Body"]))
    return paras


def _section_flowables(section: dict, image_root: Path | None) -> list:
    flow = []
    stype = section.get("type")
    if stype == "html":
        flow.extend(_html_to_paragraphs(section.get("value", "")))
    elif stype == "code":
        code_text = (section.get("value") or "").replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")
        code_text = code_text.replace("\n", "<br/>")
        flow.append(Paragraph(code_text, STYLES["CodeBlock"]))
    elif stype == "image":
        value = section.get("value", "")
        img_path = Path(value)
        if not img_path.is_absolute() and image_root:
            img_path = image_root / img_path
        if img_path.exists():
            try:
                img = Image(str(img_path))
                max_w = 15 * cm
                if img.drawWidth > max_w:
                    ratio = max_w / img.drawWidth
                    img.drawWidth *= ratio
                    img.drawHeight *= ratio
                flow.append(Spacer(1, 6))
                flow.append(img)
                if section.get("caption"):
                    flow.append(Paragraph(section["caption"], STYLES["Body"]))
                flow.append(Spacer(1, 6))
            except Exception:
                pass  # skip unreadable/remote (URL) images at PDF-build time
    return flow


def build_pdf(skeleton: dict, rewritten_dir: Path, out_path: Path, logger, image_root: Path | None = None) -> Path:
    """Walk skeleton order, pull matching rewritten JSON (matched by title,
    same normalization used elsewhere in the pipeline), and lay it all out."""
    rewritten_by_title = {}
    for f in rewritten_dir.glob("*.json"):
        data = json.loads(f.read_text())
        rewritten_by_title[data["title"].strip().lower()] = data

    doc = SimpleDocTemplate(str(out_path), pagesize=A4,
                             topMargin=2 * cm, bottomMargin=2 * cm,
                             leftMargin=2 * cm, rightMargin=2 * cm)
    story = []
    missing = 0

    for pillar in skeleton["pillars"]:
        story.append(Paragraph(f"Pillar {pillar['number']}: {pillar['title']}", STYLES["PillarTitle"]))
        for mod in pillar["modules"]:
            story.append(Paragraph(f"Module {mod['number']}: {mod['title']}", STYLES["ModuleTitle"]))
            for chap in mod["chapters"]:
                story.append(Paragraph(f"Chapter {chap['number']}: {chap['title']}", STYLES["ChapterTitle"]))
                for page in chap["pages"]:
                    title_key = page["title"].strip().lower()
                    rewritten = rewritten_by_title.get(title_key)
                    story.append(Paragraph(page["title"], STYLES["PageTitle"]))
                    if not rewritten:
                        missing += 1
                        story.append(Paragraph("<i>(content not yet generated)</i>", STYLES["Body"]))
                        continue
                    for section in rewritten.get("sections", []):
                        story.extend(_section_flowables(section, image_root))
            story.append(PageBreak())

    doc.build(story)
    if missing:
        logger.warning("%d pages had no rewritten content and were left as placeholders", missing)
    logger.info("Final PDF written to %s", out_path)
    return out_path
