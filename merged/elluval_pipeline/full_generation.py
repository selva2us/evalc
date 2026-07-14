"""
full_generation.py
===================
Drives the entire "generate + submit everything" flow that runs
automatically once a skeleton has been approved and submitted for a
Subject ID (see pipeline.py's Pipeline.generate_and_submit_everything()
and app/routes/pipeline.py's submit() route).

No Document ID is required for this -- the caller only needs to have
already resolved cfg.subject_id (see pipeline.submit_syllabus /
prompt_for_subject_id). Everything below is looked up against the real
subject tree fetched via GET /api/subjects/<subject_id>/tree.

Order of operations, exactly mirroring how a human author would build
the curriculum top-down (and matching what asset_generation.py's
APPLICABLE_LEVELS documents each asset type is scoped to):

  for each Pillar:
      generate + PUT pillar overview
      for each Module in that pillar:
          generate + PUT module overview
          for each Chapter in that module:
              generate + PUT chapter overview
              for each Page in that chapter (document order):
                  generate + POST page content
              # once every page in the chapter is done:
              generate + POST chapter FAQ
              generate + POST chapter example program
              generate + POST chapter practice program
          # once every chapter in the module is done:
          generate + PUT module flashcards
          generate + PUT module quiz

Every step is checkpointed in StateStore under its own stage name keyed
by the node's hierarchy key (see asset_generation.hierarchy_nodes), so
re-running on the same work_dir after an interruption or a partial
failure skips everything already marked "done" and only retries/repeats
what's left -- the same resumability convention every other stage in
this package already uses.

A single item's failure (a bad model response, a 4xx from the backend,
a missing id in the subject tree, ...) is logged and recorded in the
returned summary rather than aborting the whole run -- large curricula
have hundreds of items, and one bad chapter shouldn't block every page
after it.
"""
from __future__ import annotations

import json
from pathlib import Path

from . import ai_content, asset_generation as ag
from .api_client import CurriculumClient, fetch_title_lookup


def _resolve_id(lookup: dict, title: str | None) -> str | None:
    if not title:
        return None
    return lookup.get(title.strip().lower())


class _Summary:
    def __init__(self):
        self.done: dict[str, int] = {}
        self.failed: list[dict] = []

    def ok(self, kind: str):
        self.done[kind] = self.done.get(kind, 0) + 1

    def fail(self, kind: str, title: str, error: Exception | str):
        self.failed.append({"kind": kind, "title": title, "error": str(error)})

    def as_dict(self) -> dict:
        return {"done": self.done, "failed": self.failed}


def run_full_generation(skeleton: dict, cfg, logger, state, work_dir: Path | str) -> dict:
    """Generate and submit every educational asset for an already-approved,
    already-submitted skeleton: pillar/module/chapter overviews, every
    page's content, each chapter's FAQ/example/practice programs, and
    each module's flashcards/quiz. Returns a summary dict:
    {"done": {"pillar_overview": 3, "content": 120, ...}, "failed": [...]}.
    """
    work_dir = Path(work_dir)
    client = CurriculumClient(cfg, logger)

    logger.info("=== Fetching subject tree for subject_id=%s ===", cfg.subject_id)
    lookup = fetch_title_lookup(client, work_dir)

    nodes = ag.hierarchy_nodes(skeleton)
    by_key = {n["key"]: n for n in nodes}
    content_gen = ai_content.ContentGenerator(cfg, logger)
    summary = _Summary()

    def _target_id(asset_type: str, node: dict) -> str | None:
        title = ag.nearest_target_title(asset_type, node)
        return _resolve_id(lookup, title)

    def _generate_and_submit(stage: str, key: str, node: dict, asset_type: str, submit_fn):
        """Shared generate -> submit -> state.mark("done"/"failed:...")
        flow used by every non-page asset type below."""
        if state.is_done(stage, key):
            summary.ok(asset_type)
            return
        try:
            payload = ag.GENERATORS[asset_type](node["title"], node["breadcrumb"], cfg)
            target_id = _target_id(asset_type, node)
            if not target_id:
                raise RuntimeError(
                    f'No matching id found in subject tree for "{node["title"]}" '
                    f"(needed for {ag.ASSET_TYPES.get(asset_type, asset_type)})"
                )
            ok = submit_fn(target_id, payload)
            if not ok:
                raise RuntimeError("Submission rejected by the API - see server log for details.")
        except Exception as exc:
            logger.exception("%s failed for '%s': %s", ag.ASSET_TYPES.get(asset_type, asset_type), node["title"], exc)
            state.mark(stage, key, f"failed:{exc}")
            summary.fail(asset_type, node["title"], exc)
            return
        state.mark(stage, key, "done")
        summary.ok(asset_type)

    def _generate_page(key: str, title: str, breadcrumb: str):
        if state.is_done("content", key):
            summary.ok("content")
            return
        out_path = work_dir / "rewritten" / f"{key}.json"
        out_path.parent.mkdir(parents=True, exist_ok=True)
        try:
            content = content_gen.generate_page(title, breadcrumb)
            out_path.write_text(json.dumps(content, indent=2))
            page_id = _resolve_id(lookup, title)
            if not page_id:
                raise RuntimeError(f'No matching page id found in subject tree for "{title}"')
            ok = client.post_page_content(page_id, content)
            if not ok:
                raise RuntimeError("Content upload rejected by the API - see server log for details.")
        except Exception as exc:
            logger.exception("Page content failed for '%s': %s", title, exc)
            state.mark("content", key, f"failed:{exc}")
            summary.fail("content", title, exc)
            return
        state.mark("content", key, "done")
        summary.ok("content")

    page_counter = 0
    for pi, pillar in enumerate(skeleton["pillars"], start=1):
        pillar_key = f"pillar-{pi:03d}"
        pillar_node = by_key[pillar_key]
        logger.info("=== Pillar %d/%d: %s ===", pi, len(skeleton["pillars"]), pillar["title"])
        _generate_and_submit("pillar_overview", pillar_key, pillar_node, "pillar_overview", client.put_pillar_overview)

        for mi, mod in enumerate(pillar["modules"], start=1):
            module_key = f"module-{pi:03d}-{mi:03d}"
            module_node = by_key[module_key]
            logger.info("--- Module %d/%d: %s ---", mi, len(pillar["modules"]), mod["title"])
            _generate_and_submit("module_overview", module_key, module_node, "module_overview", client.put_module_overview)

            for ci, chap in enumerate(mod["chapters"], start=1):
                chapter_key = f"chapter-{pi:03d}-{mi:03d}-{ci:03d}"
                chapter_node = by_key[chapter_key]
                logger.info("Chapter %d/%d: %s", ci, len(mod["chapters"]), chap["title"])
                _generate_and_submit("chapter_overview", chapter_key, chapter_node, "chapter_overview", client.put_chapter_overview)

                # Pages, strictly in document order, one after another.
                for page in chap["pages"]:
                    page_counter += 1
                    page_key = f"{page_counter:04d}"
                    breadcrumb = (
                        f"Technology: {skeleton.get('technology_name', '')}\n"
                        f"Pillar: {pillar['title']}\nModule: {mod['title']}\n"
                        f"Chapter: {chap['title']}\nPage: {page['title']}"
                    )
                    _generate_page(page_key, page["title"], breadcrumb)

                # Only once every page in this chapter has been attempted:
                # FAQ, then Example Program, then Practice Program.
                _generate_and_submit("faq", chapter_key, chapter_node, "faq", client.post_page_content)
                _generate_and_submit("example_program", chapter_key, chapter_node, "example_program", client.post_compiler_practice)
                _generate_and_submit("practice_program", chapter_key, chapter_node, "practice_program", client.post_compiler_practice)

            # Only once every chapter in this module is done: flashcards, quiz.
            _generate_and_submit("flashcards", module_key, module_node, "flashcards", client.put_module_flashcards)
            _generate_and_submit("module_quiz", module_key, module_node, "module_quiz", client.put_module_quiz)

    logger.info("=== Full generation complete: %s ===", summary.as_dict())
    return summary.as_dict()
