"""
tree_utils.py
=============
Normalizes the raw ``GET /api/subjects/<id>/tree`` response (see
api_client.CurriculumClient.fetch_tree) into the same flat, ordered node
list asset_generation.hierarchy_nodes() produces -- except every node
here carries its **real id** straight from the tree, since the source is
the live subject tree itself rather than an AI-drafted skeleton that
then has to be title-matched against it.

This is what lets the Hub (app/routes/hub.py) work from nothing but a
Subject ID: no AI skeleton step, no separate title->id lookup pass, and
because ids are already known, "target id" is never ambiguous the way a
title-match can be (duplicate titles, near-miss titles, etc.).

CAVEAT: the exact key names the real API uses for a node's children at
each level (e.g. "modules" vs "children") weren't confirmed against the
live API when this was written -- see api_client.import_syllabus's
identical caveat about the syllabus-import payload shape. CHILD_KEYS /
ROOT_KEYS below try the most likely names, in order, for each level.
If your backend uses different names, this is the one place to fix it.
"""
from __future__ import annotations

# Which key(s) hold a node's children at each level, tried in order.
CHILD_KEYS = {
    "pillar": ["modules", "children", "items"],
    "module": ["chapters", "children", "items"],
    "chapter": ["pages", "children", "items"],
}
# Top-level key(s) holding the list of pillars in the raw tree response.
ROOT_KEYS = ["pillars", "children", "items"]


def _children(node: dict, level: str) -> list[dict]:
    for key in CHILD_KEYS.get(level, []):
        val = node.get(key)
        if isinstance(val, list):
            return val
    return []


def _title(node: dict) -> str:
    return node.get("title") or node.get("name") or ""


def _id(node: dict):
    return node.get("id") or node.get("_id") or node.get("pageId")


def normalize_tree(raw_tree, technology_name: str = "") -> dict:
    """raw_tree is whatever CurriculumClient.fetch_tree() returned -- a
    dict with a top-level pillars list, or a bare list of pillars (both
    handled). Returns {"technology_name": ..., "pillars": [...]} in the
    same shape ai_skeleton.py produces, except every node also carries a
    real "id" alongside "title"."""
    if isinstance(raw_tree, list):
        pillars_raw = raw_tree
    else:
        pillars_raw = None
        for key in ROOT_KEYS:
            val = raw_tree.get(key)
            if isinstance(val, list):
                pillars_raw = val
                break
        pillars_raw = pillars_raw or []
        technology_name = technology_name or raw_tree.get("title") or raw_tree.get("name") or ""

    pillars = []
    for p in pillars_raw:
        modules = []
        for m in _children(p, "pillar"):
            chapters = []
            for c in _children(m, "module"):
                pages = []
                for pg in _children(c, "chapter"):
                    pages.append({"id": _id(pg), "title": _title(pg)})
                chapters.append({"id": _id(c), "title": _title(c), "pages": pages})
            modules.append({"id": _id(m), "title": _title(m), "chapters": chapters})
        pillars.append({"id": _id(p), "title": _title(p), "modules": modules})

    return {"technology_name": technology_name, "pillars": pillars}


def hierarchy_nodes_with_ids(tree: dict) -> list[dict]:
    """Like asset_generation.hierarchy_nodes(), but every node also
    carries the real "id" (and its parents' real ids) from the subject
    tree, plus, for pages only, a "content_key" -- the plain zero-padded
    sequential counter (0001, 0002, ...) that ai_content.py /
    full_generation.py use as the "content" stage's state key. Computing
    the same counter here (by walking pages in the same pillar -> module
    -> chapter -> page order full_generation.py uses) is what lets the
    Hub read/write the exact same state.json entries as the automatic
    flow and the page-by-page Review Mode -- whichever one a run used
    first, the others pick up exactly where it left off."""
    technology = tree.get("technology_name", "")
    nodes = []
    page_counter = 0
    for pi, pillar in enumerate(tree["pillars"], start=1):
        nodes.append({
            "level": "pillar", "key": f"pillar-{pi:03d}", "id": pillar.get("id"),
            "title": pillar["title"],
            "breadcrumb": f"Technology: {technology}\nPillar: {pillar['title']}",
            "parent_pillar_id": None, "parent_module_id": None, "parent_chapter_id": None,
        })
        for mi, mod in enumerate(pillar["modules"], start=1):
            nodes.append({
                "level": "module", "key": f"module-{pi:03d}-{mi:03d}", "id": mod.get("id"),
                "title": mod["title"],
                "breadcrumb": f"Technology: {technology}\nPillar: {pillar['title']}\nModule: {mod['title']}",
                "parent_pillar_id": pillar.get("id"), "parent_module_id": None, "parent_chapter_id": None,
            })
            for ci, chap in enumerate(mod["chapters"], start=1):
                nodes.append({
                    "level": "chapter", "key": f"chapter-{pi:03d}-{mi:03d}-{ci:03d}", "id": chap.get("id"),
                    "title": chap["title"],
                    "breadcrumb": (
                        f"Technology: {technology}\nPillar: {pillar['title']}\n"
                        f"Module: {mod['title']}\nChapter: {chap['title']}"
                    ),
                    "parent_pillar_id": pillar.get("id"), "parent_module_id": mod.get("id"), "parent_chapter_id": None,
                })
                for gi, page in enumerate(chap["pages"], start=1):
                    page_counter += 1
                    nodes.append({
                        "level": "page", "key": f"page-{pi:03d}-{mi:03d}-{ci:03d}-{gi:03d}",
                        "content_key": f"{page_counter:04d}",
                        "id": page.get("id"),
                        "title": page["title"],
                        "breadcrumb": (
                            f"Technology: {technology}\nPillar: {pillar['title']}\nModule: {mod['title']}\n"
                            f"Chapter: {chap['title']}\nPage: {page['title']}"
                        ),
                        "parent_pillar_id": pillar.get("id"), "parent_module_id": mod.get("id"),
                        "parent_chapter_id": chap.get("id"),
                    })
    return nodes
