"""
Web front end for elluval_pipeline/asset_generation.py -- an optional,
additive "Asset Studio" for generating FAQs, example/practice programs,
chapter/module/pillar overviews, flashcards, and module quizzes, with the
same generate -> preview/edit -> approve -> submit -> skip workflow the
page-content Review Mode (app/routes/pipeline.py) already uses.

Nothing here is reachable from, or changes the behavior of, the existing
skeleton/review/submit routes or the automatic content pipeline -- it's a
separate URL tree (/pipeline/assets/...) linked from the existing result
page, entered only by explicit navigation.

Routes:
  GET  /pipeline/assets/<run_id>                        -> hub: hierarchy
                                                             + generate buttons
                                                             + pending assets
  POST /pipeline/assets/<run_id>/generate                -> generate one asset
  GET  /pipeline/assets/<run_id>/review/<asset_id>       -> preview/edit
  POST /pipeline/assets/<run_id>/review/<asset_id>/regenerate
  POST /pipeline/assets/<run_id>/review/<asset_id>/approve
  POST /pipeline/assets/<run_id>/review/<asset_id>/skip
"""
from __future__ import annotations

import json
import uuid
from pathlib import Path

from flask import Blueprint, flash, redirect, render_template, request, url_for

from app.pipeline_config import work_dir_for
from app.routes.pipeline import _load_pipeline, _load_run_meta
from elluval_pipeline import asset_generation as ag
from elluval_pipeline.api_client import CurriculumClient, fetch_title_lookup

assets_bp = Blueprint("assets", __name__)


# ---------------------------------------------------------------------
# Hierarchy + id-lookup helpers (shared with full_generation.py -- see
# elluval_pipeline/asset_generation.py for hierarchy_nodes / TARGET_LEVEL /
# nearest_target_title).
# ---------------------------------------------------------------------
_hierarchy_nodes = ag.hierarchy_nodes
_TARGET_LEVEL = ag.TARGET_LEVEL
_nearest_target_title = ag.nearest_target_title


def _title_lookup(pipe, work_dir: Path) -> dict:
    """title(lowercased) -> id for every node in the real subject tree.
    Cached to disk (page_lookup.json), shared with page-content Review
    Mode's and full_generation.py's identical cache."""
    client = CurriculumClient(pipe.cfg, pipe.logger)
    return fetch_title_lookup(client, work_dir)


# ---------------------------------------------------------------------
# Asset file storage: work_dir/assets/<asset_id>.json
# ---------------------------------------------------------------------
def _assets_dir(work_dir: Path) -> Path:
    d = work_dir / "assets"
    d.mkdir(parents=True, exist_ok=True)
    return d


def _save_asset(work_dir: Path, asset_id: str, record: dict) -> None:
    (_assets_dir(work_dir) / f"{asset_id}.json").write_text(json.dumps(record, indent=2))


def _load_asset(work_dir: Path, asset_id: str) -> dict | None:
    path = _assets_dir(work_dir) / f"{asset_id}.json"
    return json.loads(path.read_text()) if path.exists() else None


def _all_assets(work_dir: Path) -> list[dict]:
    out = []
    for path in sorted(_assets_dir(work_dir).glob("*.json")):
        record = json.loads(path.read_text())
        record["asset_id"] = path.stem
        out.append(record)
    return out


def _submit_asset(pipe, asset_type: str, target_id: str, payload) -> bool:
    client = CurriculumClient(pipe.cfg, pipe.logger)
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


# ---------------------------------------------------------------------
# Routes
# ---------------------------------------------------------------------
@assets_bp.get("/<run_id>")
def hub(run_id: str):
    work_dir = work_dir_for(run_id)
    skeleton_path = work_dir / "skeleton.json"
    if not skeleton_path.exists():
        flash("No skeleton found for that run. Start a new one.", "error")
        return redirect(url_for("pipeline.index"))

    skeleton = json.loads(skeleton_path.read_text())
    nodes = _hierarchy_nodes(skeleton)
    assets_by_node_key = {}
    for record in _all_assets(work_dir):
        assets_by_node_key.setdefault(record["node_key"], []).append(record)

    return render_template(
        "assets/hub.html",
        run_id=run_id,
        technology_name=skeleton.get("technology_name", ""),
        nodes=nodes,
        asset_types=ag.ASSET_TYPES,
        applicable_levels=ag.APPLICABLE_LEVELS,
        assets_by_node_key=assets_by_node_key,
    )


@assets_bp.post("/<run_id>/generate")
def generate(run_id: str):
    work_dir = work_dir_for(run_id)
    node_level = request.form.get("node_level", "")
    node_key = request.form.get("node_key", "")
    node_title = request.form.get("node_title", "")
    breadcrumb = request.form.get("breadcrumb", "")
    asset_type = request.form.get("asset_type", "")

    if asset_type not in ag.GENERATORS or node_level not in ag.APPLICABLE_LEVELS.get(asset_type, []):
        flash("That asset type isn't applicable at that level.", "error")
        return redirect(url_for("assets.hub", run_id=run_id))

    pipe = _load_pipeline(run_id)
    meta = _load_run_meta(work_dir)
    pipe.cfg.subject_id = meta.get("subject_id")

    try:
        payload = ag.GENERATORS[asset_type](node_title, breadcrumb, pipe.cfg)
    except Exception as exc:
        flash(f"Generation failed for {ag.ASSET_TYPES.get(asset_type, asset_type)}: {exc}", "error")
        return redirect(url_for("assets.hub", run_id=run_id))

    asset_id = uuid.uuid4().hex[:10]
    _save_asset(work_dir, asset_id, {
        "asset_type": asset_type,
        "node_level": node_level,
        "node_key": node_key,
        "node_title": node_title,
        "breadcrumb": breadcrumb,
        "payload": payload,
        "status": "pending",
    })
    return redirect(url_for("assets.review", run_id=run_id, asset_id=asset_id))


@assets_bp.get("/<run_id>/review/<asset_id>")
def review(run_id: str, asset_id: str):
    work_dir = work_dir_for(run_id)
    record = _load_asset(work_dir, asset_id)
    if not record:
        flash("That asset no longer exists.", "error")
        return redirect(url_for("assets.hub", run_id=run_id))

    skeleton = json.loads((work_dir / "skeleton.json").read_text())
    node = next((n for n in _hierarchy_nodes(skeleton) if n["key"] == record["node_key"]), None)

    suggested_target_id = ""
    try:
        pipe = _load_pipeline(run_id)
        meta = _load_run_meta(work_dir)
        pipe.cfg.subject_id = meta.get("subject_id")
        lookup = _title_lookup(pipe, work_dir)
        target_title = _nearest_target_title(record["asset_type"], node) if node else None
        if target_title:
            suggested_target_id = lookup.get(target_title.strip().lower(), "")
    except Exception:
        pass  # best-effort only; reviewer can always type the id in manually

    payload = record["payload"]
    is_list_payload = isinstance(payload, list)

    return render_template(
        "assets/review.html",
        run_id=run_id,
        asset_id=asset_id,
        asset_type=record["asset_type"],
        asset_type_label=ag.ASSET_TYPES.get(record["asset_type"], record["asset_type"]),
        target_level=_TARGET_LEVEL[record["asset_type"]] or record["node_level"],
        node_title=record["node_title"],
        node_level=record["node_level"],
        breadcrumb=record["breadcrumb"],
        status=record["status"],
        payload_json=json.dumps(payload, indent=2),
        is_list_payload=is_list_payload,
        suggested_target_id=suggested_target_id,
    )


@assets_bp.post("/<run_id>/review/<asset_id>/regenerate")
def regenerate(run_id: str, asset_id: str):
    work_dir = work_dir_for(run_id)
    record = _load_asset(work_dir, asset_id)
    if not record:
        flash("That asset no longer exists.", "error")
        return redirect(url_for("assets.hub", run_id=run_id))

    pipe = _load_pipeline(run_id)
    meta = _load_run_meta(work_dir)
    pipe.cfg.subject_id = meta.get("subject_id")

    try:
        record["payload"] = ag.GENERATORS[record["asset_type"]](
            record["node_title"], record["breadcrumb"], pipe.cfg
        )
        record["status"] = "pending"
        _save_asset(work_dir, asset_id, record)
    except Exception as exc:
        flash(f"Regeneration failed: {exc}", "error")

    return redirect(url_for("assets.review", run_id=run_id, asset_id=asset_id))


@assets_bp.post("/<run_id>/review/<asset_id>/approve")
def approve(run_id: str, asset_id: str):
    work_dir = work_dir_for(run_id)
    record = _load_asset(work_dir, asset_id)
    if not record:
        flash("That asset no longer exists.", "error")
        return redirect(url_for("assets.hub", run_id=run_id))

    target_id = request.form.get("target_id", "").strip()
    raw = request.form.get("payload_json", "")
    if not target_id:
        flash("A target ID is required before approving.", "error")
        return redirect(url_for("assets.review", run_id=run_id, asset_id=asset_id))

    try:
        payload = json.loads(raw)
    except Exception:
        flash("That content isn't valid JSON -- fix it and try again.", "error")
        return redirect(url_for("assets.review", run_id=run_id, asset_id=asset_id))

    record["payload"] = payload
    pipe = _load_pipeline(run_id)
    meta = _load_run_meta(work_dir)
    pipe.cfg.subject_id = meta.get("subject_id")

    try:
        ok = _submit_asset(pipe, record["asset_type"], target_id, payload)
        if not ok:
            raise RuntimeError("Upload rejected by the API - see server log for details.")
    except Exception as exc:
        record["status"] = f"failed:{exc}"
        _save_asset(work_dir, asset_id, record)
        flash(f"Edits saved locally, but submission failed: {exc}", "error")
        return redirect(url_for("assets.review", run_id=run_id, asset_id=asset_id))

    record["status"] = "done"
    _save_asset(work_dir, asset_id, record)
    flash(f"{ag.ASSET_TYPES.get(record['asset_type'], record['asset_type'])} submitted.", "success")
    return redirect(url_for("assets.hub", run_id=run_id))


@assets_bp.post("/<run_id>/review/<asset_id>/skip")
def skip(run_id: str, asset_id: str):
    work_dir = work_dir_for(run_id)
    record = _load_asset(work_dir, asset_id)
    if record:
        record["status"] = "skipped"
        _save_asset(work_dir, asset_id, record)
    return redirect(url_for("assets.hub", run_id=run_id))
