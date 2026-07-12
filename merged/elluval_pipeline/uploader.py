"""
uploader.py
===========
Stage 4: match each rewritten page (by title) to the real page id in the
subject's curriculum tree, upload any local images it references, and POST
the final sections payload to the content API.

This merges migrate_content.rb (tree fetch + title matching) and
upload_content.rb (image upload + content POST) into one consistent path,
using the shared CurriculumClient instead of three separate ad-hoc HTTParty
setups.
"""
from __future__ import annotations

import json
from pathlib import Path

from .api_client import CurriculumClient, collect_pages


def upload_all(rewritten_dir: Path, cfg, logger, state=None) -> None:
    client = CurriculumClient(cfg, logger)
    tree = client.fetch_tree()
    lookup: dict = {}
    collect_pages(tree, lookup)
    logger.info("Found %d pages in subject tree", len(lookup))

    files = sorted(rewritten_dir.glob("*.json"))
    logger.info("Found %d rewritten page files", len(files))

    for f in files:
        key = f.stem
        if state and state.is_done("upload", key):
            logger.info("Skipping upload for '%s' (already uploaded)", key)
            continue

        payload = json.loads(f.read_text())
        title = (payload.get("title") or "").strip().lower()
        if not title:
            logger.warning("Skipping %s - title missing", f)
            continue

        page_id = lookup.get(title)
        if not page_id:
            logger.warning('Page not found in subject tree: "%s" - skipping', payload["title"])
            if state:
                state.mark("upload", key, "failed:no_matching_page")
            continue

        logger.info("Processing: %s (%s)", payload["title"], page_id)

        for section in payload.get("sections", []):
            if section.get("type") != "image":
                continue
            value = str(section.get("value", ""))
            if value.startswith("http"):
                continue  # already uploaded
            image_path = Path(value)
            if not image_path.exists():
                logger.error("Image not found: %s", image_path)
                continue
            url = client.upload_image(image_path)
            if url:
                section["value"] = url
            else:
                logger.error("Image upload failed for %s; section may be rejected", image_path)

        ok = client.post_page_content(page_id, payload)
        if state:
            state.mark("upload", key, "done" if ok else "failed:content_post")
