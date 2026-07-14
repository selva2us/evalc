"""
Curriculum Hub -- a single screen driven by nothing but a Subject ID.

Unlike the AI-skeleton flow (app/routes/pipeline.py) and the automatic
generator (elluval_pipeline/full_generation.py), the Hub doesn't draft a
skeleton with AI first: it fetches the *real* subject tree straight from
GET /api/subjects/<id>/tree (see elluval_pipeline/tree_utils.py) and
shows every pillar/module/chapter/page in one screen, each with its own
status badge and its own Generate / Review / Approve / Regenerate
controls -- so you can jump to any single item directly instead of
walking through them one at a time, and a failed item (bad model
response, AI quota exhausted, a rejected upload, ...) never blocks or
loses progress on anything else.

It reads/writes the exact same state.json stage names
(elluval_pipeline/full_generation.py's "content", "pillar_overview",
"module_overview", "chapter_overview", "faq", "example_program",
"practice_program", "flashcards", "module_quiz") that the automatic
flow and the Asset Studio already use, so the three ways of driving a
run are fully interchangeable: start a run with "Generate Everything",
let it run out of AI quota partway through, then finish the rest by
hand here -- or the other way around. Whichever already-done items exist
show as done immediately.

Routes:
  GET  /pipeline/hub                                    -> subject_id form
  POST /pipeline/hub                                    -> fetch tree, create/reuse run
  GET  /pipeline/hub/<run_id>                            -> the board (full tree + statuses)
  POST /pipeline/hub/<run_id>/generate/<asset_type>/<node_key>   -> generate one item
  GET  /pipeline/hub/<run_id>/review/<asset_type>/<node_key>     -> preview/edit one item
  POST /pipeline/hub/<run_id>/review/<asset_type>/<node_key>/approve
  POST /pipeline/hub/<run_id>/review/<asset_type>/<node_key>/regenerate
  POST /pipeline/hub/<run_id>/skip/<asset_type>/<node_key>
"""
from __future__ import annotations

import json
import re
from pathlib import Path

from flask import Blueprint, flash, redirect, render_template, request, url_for

from app.pipeline_config import work_dir_for
from app.routes.pipeline import _load_pipeline, _load_run_meta, _save_run_meta
from elluval_pipeline import ai_content, asset_generation as ag, tree_utils as tu
from elluval_pipeline.api_client import CurriculumClient

hub_bp = Blueprint("hub", __name__)

# Which asset type(s) show on the board for each hierarchy level, in the
# exact order/state-key convention full_generation.py already uses (so a
# run started in auto mode and finished here -- or vice versa -- reads
# the same checkmarks). Extra, optional asset types (e.g. a module- or
# page-level FAQ) stay reachable through the existing Asset Studio.
BOARD_ASSET_TYPES: dict[str, list[str]] = {
    "pillar": ["pillar_overview"],
    "module": ["module_overview", "flashcards", "module_quiz"],
    "chapter": ["chapter_overview", "faq", "example_program", "practice_program"],
    "page": ["content"],
}

LABELS: dict[str, str] = {
    "content": "Page Content",
    **ag.ASSET_TYPES,
}


def _run_id_for_subject(subject_id: str) -> str:
    """Deterministic, filesystem-safe run id from a Subject ID, so
    re-entering the same Subject ID later (a new day, a new tab, after
    quota comes back) always lands back on the exact same run/work_dir/
    state.json instead of starting a fresh, empty board."""
    safe = re.sub(r"[^a-zA-Z0-9_-]+", "-", subject_id.strip()).strip("-") or "run"
    return f"subject-{safe}"


def _tree_path(work_dir: Path) -> Path:
    return work_dir / "hub_tree.json"


def _load_tree(work_dir: Path) -> dict | None:
    path = _tree_path(work_dir)
    return json.loads(path.read_text()) if path.exists() else None


def _nodes_for(work_dir: Path) -> list[dict]:
    tree = _load_tree(work_dir)
    return tu.hierarchy_nodes_with_ids(tree) if tree else []


def _find_node(nodes: list[dict], node_key: str) -> dict | None:
    return next((n for n in nodes if n["key"] == node_key), None)


def _state_key(node: dict, asset_type: str) -> str:
    return node["content_key"] if asset_type == "content" else node["key"]


def _target_id(node: dict, asset_type: str) -> str | None:
    if asset_type == "content":
        return node.get("id")
    needed = ag.TARGET_LEVEL.get(asset_type)
    if needed is None or node["level"] == needed:
        return node.get("id")
    return node.get(f"parent_{needed}_id")


def _drafts_dir(work_dir: Path) -> Path:
    d = work_dir / "hub_drafts"
    d.mkdir(parents=True, exist_ok=True)
    return d


def _draft_path(work_dir: Path, asset_type: str, node_key: str) -> Path:
    return _drafts_dir(work_dir) / f"{asset_type}__{node_key}.json"


def _save_draft(work_dir: Path, asset_type: str, node_key: str, record: dict) -> None:
    _draft_path(work_dir, asset_type, node_key).write_text(json.dumps(record, indent=2))


def _load_draft(work_dir: Path, asset_type: str, node_key: str) -> dict | None:
    path = _draft_path(work_dir, asset_type, node_key)
    return json.loads(path.read_text()) if path.exists() else None


def _submit_asset(pipe, asset_type: str, target_id: str, payload) -> bool:
    client = CurriculumClient(pipe.cfg, pipe.logger)
    if asset_type == "content":
        return client.post_page_content(target_id, payload)
    if asset_type == "faq":
        return client.post_page_content(target_id, payload)
    if asset_type in ("example_program", "practice_program"):
        return client.post_compiler_practice(target_id, payload)
    if asset_type == "chapter_overview":
        return client.put_chapter_overview(target_id, payload)
    if asset_type == "module_overview":
        return client.put_module_overview(target_id, payload)
    if asset_type == "pillar_overview":
        return client.put_pillar_overview(target_id, payload)
    if asset_type == "flashcards":
        return client.put_module_flashcards(target_id, payload)
    if asset_type == "module_quiz":
        return client.put_module_quiz(target_id, payload)
    raise ValueError(f"Unknown asset type: {asset_type}")


def _generate_payload(pipe, node: dict, asset_type: str):
    if asset_type == "content":
        gen = ai_content.ContentGenerator(pipe.cfg, pipe.logger)
        return gen.generate_page(node["title"], node["breadcrumb"])
    return ag.GENERATORS[asset_type](node["title"], node["breadcrumb"], pipe.cfg)


# ---------------------------------------------------------------------
# Routes
# ---------------------------------------------------------------------
@hub_bp.get("/")
def start():
    return render_template("hub/start.html")


@hub_bp.post("/")
def load():
    subject_id = request.form.get("subject_id", "").strip()
    if not subject_id:
        flash("Subject ID is required.", "error")
        return redirect(url_for("hub.start"))

    run_id = _run_id_for_subject(subject_id)
    work_dir = work_dir_for(run_id)
    pipe = _load_pipeline(run_id)
    pipe.cfg.subject_id = subject_id
    _save_run_meta(work_dir, subject_id=subject_id, mode="hub")

    try:
        client = CurriculumClient(pipe.cfg, pipe.logger)
        raw_tree = client.fetch_tree(subject_id)
        tree = tu.normalize_tree(raw_tree)
        if not tree["pillars"]:
            raise RuntimeError("The subject tree came back empty -- check the Subject ID.")
        _tree_path(work_dir).write_text(json.dumps(tree, indent=2))
    except Exception as exc:
        flash(f"Couldn't load that subject: {exc}", "error")
        return redirect(url_for("hub.start"))

    return redirect(url_for("hub.board", run_id=run_id))


@hub_bp.get("/<run_id>")
def board(run_id: str):
    work_dir = work_dir_for(run_id)
    meta = _load_run_meta(work_dir)
    tree = _load_tree(work_dir)
    if not tree:
        flash("No subject loaded for that run -- start again with a Subject ID.", "error")
        return redirect(url_for("hub.start"))

    nodes = tu.hierarchy_nodes_with_ids(tree)
    pipe = _load_pipeline(run_id)

    rows = []
    totals = {"done": 0, "failed": 0, "pending": 0, "not_started": 0}
    for node in nodes:
        items = []
        for asset_type in BOARD_ASSET_TYPES[node["level"]]:
            state_key = _state_key(node, asset_type)
            raw_status = pipe.state.get_stage(asset_type).get(state_key)
            draft = _load_draft(work_dir, asset_type, node["key"])
            if raw_status == "done":
                bucket = "done"
            elif isinstance(raw_status, str) and raw_status.startswith("failed"):
                bucket = "failed"
            elif raw_status == "skipped":
                bucket = "skipped"
            elif draft is not None:
                bucket = "pending"
            else:
                bucket = "not_started"
            totals[bucket] = totals.get(bucket, 0) + 1
            items.append({
                "asset_type": asset_type,
                "label": LABELS[asset_type],
                "bucket": bucket,
                "raw_status": raw_status,
            })
        rows.append({"node": node, "entries": items})

    return render_template(
        "hub/board.html",
        run_id=run_id,
        subject_id=meta.get("subject_id", ""),
        technology_name=tree.get("technology_name", ""),
        rows=rows,
        totals=totals,
    )


@hub_bp.post("/<run_id>/generate/<asset_type>/<node_key>")
def generate(run_id: str, asset_type: str, node_key: str):
    work_dir = work_dir_for(run_id)
    meta = _load_run_meta(work_dir)
    nodes = _nodes_for(work_dir)
    node = _find_node(nodes, node_key)
    if not node or asset_type not in BOARD_ASSET_TYPES.get(node["level"], []):
        flash("That item doesn't exist in this run's tree.", "error")
        return redirect(url_for("hub.board", run_id=run_id))

    pipe = _load_pipeline(run_id)
    pipe.cfg.subject_id = meta.get("subject_id")

    try:
        payload = _generate_payload(pipe, node, asset_type)
    except Exception as exc:
        flash(f"Generation failed for {LABELS.get(asset_type, asset_type)}: {exc}", "error")
        return redirect(url_for("hub.board", run_id=run_id))

    _save_draft(work_dir, asset_type, node_key, {
        "asset_type": asset_type,
        "node_key": node_key,
        "node_level": node["level"],
        "title": node["title"],
        "breadcrumb": node["breadcrumb"],
        "payload": payload,
        "target_id": _target_id(node, asset_type) or "",
        "status": "pending",
    })
    return redirect(url_for("hub.review", run_id=run_id, asset_type=asset_type, node_key=node_key))


@hub_bp.get("/<run_id>/review/<asset_type>/<node_key>")
def review(run_id: str, asset_type: str, node_key: str):
    work_dir = work_dir_for(run_id)
    draft = _load_draft(work_dir, asset_type, node_key)
    if not draft:
        flash("Nothing generated yet for that item -- generate it from the board first.", "error")
        return redirect(url_for("hub.board", run_id=run_id))

    payload = draft["payload"]
    return render_template(
        "hub/review.html",
        run_id=run_id,
        asset_type=asset_type,
        node_key=node_key,
        asset_type_label=LABELS.get(asset_type, asset_type),
        node_level=draft["node_level"],
        node_title=draft["title"],
        breadcrumb=draft["breadcrumb"],
        status=draft["status"],
        payload_json=json.dumps(payload, indent=2),
        is_list_payload=isinstance(payload, list),
        target_id=draft.get("target_id", ""),
    )


@hub_bp.post("/<run_id>/review/<asset_type>/<node_key>/regenerate")
def regenerate(run_id: str, asset_type: str, node_key: str):
    work_dir = work_dir_for(run_id)
    path = _draft_path(work_dir, asset_type, node_key)
    if path.exists():
        path.unlink()
    return generate(run_id, asset_type, node_key)


@hub_bp.post("/<run_id>/review/<asset_type>/<node_key>/approve")
def approve(run_id: str, asset_type: str, node_key: str):
    work_dir = work_dir_for(run_id)
    meta = _load_run_meta(work_dir)
    draft = _load_draft(work_dir, asset_type, node_key)
    if not draft:
        flash("That item no longer has a draft -- generate it again.", "error")
        return redirect(url_for("hub.board", run_id=run_id))

    target_id = request.form.get("target_id", "").strip()
    raw = request.form.get("payload_json", "")
    if not target_id:
        flash("A target ID is required before approving.", "error")
        return redirect(url_for("hub.review", run_id=run_id, asset_type=asset_type, node_key=node_key))
    try:
        payload = json.loads(raw)
    except Exception:
        flash("That content isn't valid JSON -- fix it and try again.", "error")
        return redirect(url_for("hub.review", run_id=run_id, asset_type=asset_type, node_key=node_key))

    draft["payload"] = payload
    draft["target_id"] = target_id

    nodes = _nodes_for(work_dir)
    node = _find_node(nodes, node_key)
    state_key = _state_key(node, asset_type) if node else node_key

    pipe = _load_pipeline(run_id)
    pipe.cfg.subject_id = meta.get("subject_id")

    try:
        ok = _submit_asset(pipe, asset_type, target_id, payload)
        if not ok:
            raise RuntimeError("Upload rejected by the API -- see server log for details.")
    except Exception as exc:
        draft["status"] = f"failed:{exc}"
        _save_draft(work_dir, asset_type, node_key, draft)
        pipe.state.mark(asset_type, state_key, f"failed:{exc}")
        flash(f"Draft saved locally, but submission failed: {exc}", "error")
        return redirect(url_for("hub.review", run_id=run_id, asset_type=asset_type, node_key=node_key))

    draft["status"] = "done"
    _save_draft(work_dir, asset_type, node_key, draft)
    pipe.state.mark(asset_type, state_key, "done")
    flash(f"{LABELS.get(asset_type, asset_type)} submitted.", "success")
    return redirect(url_for("hub.board", run_id=run_id))


@hub_bp.post("/<run_id>/skip/<asset_type>/<node_key>")
def skip(run_id: str, asset_type: str, node_key: str):
    work_dir = work_dir_for(run_id)
    nodes = _nodes_for(work_dir)
    node = _find_node(nodes, node_key)
    if not node:
        flash("That item doesn't exist in this run's tree.", "error")
        return redirect(url_for("hub.board", run_id=run_id))

    pipe = _load_pipeline(run_id)
    pipe.state.mark(asset_type, _state_key(node, asset_type), "skipped")
    return redirect(url_for("hub.board", run_id=run_id))
