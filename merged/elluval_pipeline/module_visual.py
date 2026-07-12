"""
module_visual.py
=================
Port of module_visual.rb: for each module in the subject tree, find a
matching pre-made diagram image (module_NN_*.png in an image folder) and
PUT it into that module's overview via the curriculum API.

If you'd rather generate new diagrams than use pre-made ones, swap
`find_module_image` for a call into media.py's diagram generation hook.
"""
from __future__ import annotations

import glob
import os
from pathlib import Path

from .api_client import CurriculumClient

DEFAULT_IMAGE_FOLDER = "module_diagrams_png"


def find_module_image(image_folder: str, module_number: int) -> str | None:
    pattern = os.path.join(image_folder, f"module_{module_number:02d}_*")
    matches = sorted(glob.glob(pattern))
    return matches[0] if matches else None


def _iter_modules(tree: dict):
    modules = tree.get("modules") or tree.get("data") or tree
    if isinstance(modules, dict):
        modules = list(modules.values())
    for index, mod in enumerate(modules):
        module_number = int(mod.get("moduleNumber") or mod.get("number") or mod.get("order") or index + 1)
        yield mod, module_number


def apply_module_visuals(cfg, logger, image_folder: str = DEFAULT_IMAGE_FOLDER, state=None) -> None:
    client = CurriculumClient(cfg, logger)
    tree = client.fetch_tree()

    for mod, module_number in _iter_modules(tree):
        module_id = mod.get("id")
        key = str(module_id)
        if state and state.is_done("module_visual", key):
            logger.info("Skipping module %s (already done)", module_id)
            continue

        image_path = find_module_image(image_folder, module_number)
        if not image_path:
            logger.warning("No image found for module %d", module_number)
            continue

        image_url = client.upload_image(image_path)
        if not image_url:
            logger.warning("Skipping module %s - image upload failed", module_id)
            if state:
                state.mark("module_visual", key, "failed:upload")
            continue

        payload = {
            "overviewSummary": "",
            "overviewHtml": (
                f'<figure class="page-image-container image-size-original" style="text-align:center;">'
                f'<img src="{image_url}" alt="" /></figure>'
            ),
            "overviewPublished": False,
            "overviewHighlights": [],
        }
        ok = client.put_module_overview(module_id, payload)
        if state:
            state.mark("module_visual", key, "done" if ok else "failed:put")
