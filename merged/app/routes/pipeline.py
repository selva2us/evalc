"""
Web front end for elluval_pipeline's AI-driven flow (cli_ai.py / Pipeline.run_ai),
reworked from a terminal prompt-and-confirm flow into a multi-step web wizard:

  1. GET  /pipeline/                      -> form: technology name + notes
  2. POST /pipeline/skeleton               -> generates the skeleton, redirects to review
  3. GET  /pipeline/review/<run_id>        -> shows skeleton.md, asks for document_id
  4. POST /pipeline/submit/<run_id>        -> submits syllabus, generates page content,
                                               uploads it, shows a result summary

Every stage is delegated straight to elluval_pipeline.pipeline.Pipeline. Its
StateStore (work_dir/state.json) makes each stage resumable, which is what
lets a stateless request/redirect cycle drive a multi-stage, potentially
slow pipeline: each request just re-opens Pipeline(work_dir=...) and calls
the next stage.
"""
from __future__ import annotations

import json

from flask import Blueprint, flash, redirect, render_template, request, url_for

from app.pipeline_config import new_run_id, work_dir_for
from elluval_pipeline.pipeline import Pipeline

pipeline_bp = Blueprint("pipeline", __name__)


def _load_pipeline(run_id: str) -> Pipeline:
    return Pipeline(work_dir=str(work_dir_for(run_id)))


@pipeline_bp.get("/")
def index():
    return render_template("pipeline/index.html")


@pipeline_bp.post("/skeleton")
def generate_skeleton():
    technology_name = request.form.get("technology_name", "").strip()
    notes = request.form.get("notes", "").strip() or None

    if not technology_name:
        flash("Please enter a technology name.", "error")
        return redirect(url_for("pipeline.index"))

    run_id = new_run_id()
    pipe = _load_pipeline(run_id)

    try:
        pipe.generate_skeleton_ai(technology_name, notes=notes)
    except Exception as exc:  # RuntimeError from missing key, bad model output, etc.
        flash(f"Skeleton generation failed: {exc}", "error")
        return redirect(url_for("pipeline.index"))

    return redirect(url_for("pipeline.review", run_id=run_id))


@pipeline_bp.get("/review/<run_id>")
def review(run_id: str):
    work_dir = work_dir_for(run_id)
    skeleton_md_path = work_dir / "skeleton.md"
    skeleton_json_path = work_dir / "skeleton.json"

    if not skeleton_md_path.exists() or not skeleton_json_path.exists():
        flash("No skeleton found for that run. Start a new one.", "error")
        return redirect(url_for("pipeline.index"))

    skeleton = json.loads(skeleton_json_path.read_text())
    markdown = skeleton_md_path.read_text()

    n_modules = sum(len(p["modules"]) for p in skeleton["pillars"])
    n_chapters = sum(len(m["chapters"]) for p in skeleton["pillars"] for m in p["modules"])
    n_pages = sum(
        len(c["pages"]) for p in skeleton["pillars"] for m in p["modules"] for c in m["chapters"]
    )
    counts = {
        "pillars": len(skeleton["pillars"]),
        "modules": n_modules,
        "chapters": n_chapters,
        "pages": n_pages,
    }

    return render_template(
        "pipeline/review.html",
        run_id=run_id,
        technology_name=skeleton.get("technology_name", ""),
        markdown=markdown,
        counts=counts,
    )


@pipeline_bp.post("/submit/<run_id>")
def submit(run_id: str):
    work_dir = work_dir_for(run_id)
    skeleton_json_path = work_dir / "skeleton.json"

    if not skeleton_json_path.exists():
        flash("No skeleton found for that run. Start a new one.", "error")
        return redirect(url_for("pipeline.index"))

    document_id = request.form.get("document_id", "").strip()
    subject_id = request.form.get("subject_id", "").strip()

    if not document_id or not subject_id:
        flash("Both Document ID and Subject ID are required to submit.", "error")
        return redirect(url_for("pipeline.review", run_id=run_id))

    skeleton = json.loads(skeleton_json_path.read_text())
    pipe = _load_pipeline(run_id)
    # Setting subject_id on the config up front makes prompt_for_subject_id()
    # a no-op (it only prompts when cfg.subject_id is falsy) -- this is how
    # we skip the CLI's interactive input() in a web context.
    pipe.cfg.subject_id = subject_id

    try:
        pipe.submit_syllabus(skeleton, document_id=document_id)
        pipe.generate_content_ai(skeleton)
        pipe.upload()
    except Exception as exc:
        flash(f"Pipeline run failed: {exc}", "error")
        return render_template(
            "pipeline/result.html",
            run_id=run_id,
            technology_name=skeleton.get("technology_name", ""),
            success=False,
            error=str(exc),
        )

    return render_template(
        "pipeline/result.html",
        run_id=run_id,
        technology_name=skeleton.get("technology_name", ""),
        success=True,
        document_id=document_id,
        subject_id=subject_id,
    )
