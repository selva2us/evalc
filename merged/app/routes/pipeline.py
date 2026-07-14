"""
Web front end for elluval_pipeline's AI-driven flow (cli_ai.py / Pipeline.run_ai),
reworked from a terminal prompt-and-confirm flow into a multi-step web wizard:

  1. GET  /pipeline/                      -> form: technology name + notes
  2. POST /pipeline/skeleton               -> generates the skeleton, redirects to review
  3. GET  /pipeline/review/<run_id>        -> shows skeleton.md, asks for subject_id
                                               (no document_id needed -- subject_id
                                               alone is enough; see submit() below)
  4. POST /pipeline/submit/<run_id>        -> submits syllabus, then either:
       mode=auto   (default): fetches the subject tree and generates +
                    submits the entire curriculum -- pillar/module/chapter
                    overviews, every page's content, each chapter's FAQ and
                    example/practice programs, and each module's flashcards
                    and quiz -- see elluval_pipeline/full_generation.py.
       mode=review (optional):            hands off to the page-by-page
                    Review Mode routes below instead of auto-generating
                    (page content only).

  Review Mode (new, optional; only reachable by explicitly choosing it on
  the review page):
  5. GET  /pipeline/pages/<run_id>                 -> jump to next unhandled page
  6. GET  /pipeline/pages/<run_id>/<key>           -> generate (if needed) + show
                                                       one page's content for editing
  7. POST /pipeline/pages/<run_id>/<key>/regenerate -> discard and regenerate that page
  8. POST /pipeline/pages/<run_id>/<key>/approve    -> save edits, upload just this
                                                       page, advance to the next one
  9. POST /pipeline/pages/<run_id>/<key>/skip       -> leave this page un-uploaded,
                                                       advance to the next one
 10. GET  /pipeline/pages/<run_id>/complete         -> summary once every page has
                                                       been approved or skipped

Every stage is delegated straight to elluval_pipeline.pipeline.Pipeline (and,
for single-page generation/upload in Review Mode, straight to the same
elluval_pipeline.ai_content.ContentGenerator / api_client.CurriculumClient
classes the automatic flow already uses -- the page content payload shape
and the /api/pages/<id>/content endpoint are untouched). Its StateStore
(work_dir/state.json) makes each stage resumable, which is what lets a
stateless request/redirect cycle drive a multi-stage, potentially slow
pipeline: each request just re-opens Pipeline(work_dir=...) and calls the
next stage.
"""
from __future__ import annotations

import json
from pathlib import Path

from flask import Blueprint, flash, redirect, render_template, request, url_for

from app.pipeline_config import new_run_id, work_dir_for
from elluval_pipeline.ai_content import ContentGenerator, _breadcrumb_context
from elluval_pipeline.api_client import CurriculumClient, fetch_title_lookup
from elluval_pipeline.pipeline import Pipeline

pipeline_bp = Blueprint("pipeline", __name__)


def _load_pipeline(run_id: str) -> Pipeline:
    return Pipeline(work_dir=str(work_dir_for(run_id)))


# ---------------------------------------------------------------------
# Run metadata persisted to disk (document_id / subject_id / mode) so
# that Review Mode's per-page routes -- each a fresh, stateless request --
# can rebuild a Pipeline's cfg.subject_id without re-asking the user.
# Kept entirely separate from elluval_pipeline's own state.json.
# ---------------------------------------------------------------------
def _run_meta_path(work_dir: Path) -> Path:
    return work_dir / "run_meta.json"


def _save_run_meta(work_dir: Path, **kwargs) -> dict:
    path = _run_meta_path(work_dir)
    meta = json.loads(path.read_text()) if path.exists() else {}
    meta.update(kwargs)
    path.write_text(json.dumps(meta, indent=2))
    return meta


def _load_run_meta(work_dir: Path) -> dict:
    path = _run_meta_path(work_dir)
    return json.loads(path.read_text()) if path.exists() else {}


def _page_order(skeleton: dict) -> list[tuple[str, str, str]]:
    """[(key, title, breadcrumb), ...] in document order, same convention
    (zero-padded index) ai_content.py already uses for filenames."""
    return list(_breadcrumb_context(skeleton))


def _page_lookup(pipe: Pipeline, work_dir: Path) -> dict:
    """title(lowercased) -> page id in the real subject tree, cached to
    disk so Review Mode doesn't refetch the whole tree on every page."""
    client = CurriculumClient(pipe.cfg, pipe.logger)
    return fetch_title_lookup(client, work_dir)


def _is_handled(pipe: Pipeline, key: str) -> bool:
    status = pipe.state.get_stage("review_upload").get(key)
    return status in ("done", "skipped")


def _generate_or_load_page(pipe: Pipeline, key: str, title: str, breadcrumb: str) -> dict:
    out_path = pipe.cfg.work_dir / "rewritten" / f"{key}.json"
    if out_path.exists():
        return json.loads(out_path.read_text())
    out_path.parent.mkdir(parents=True, exist_ok=True)
    generator = ContentGenerator(pipe.cfg, pipe.logger)
    content = generator.generate_page(title, breadcrumb)
    out_path.write_text(json.dumps(content, indent=2))
    pipe.state.mark("content", key, "done")
    return content


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

    # Document ID is no longer collected from the user -- Subject ID alone
    # is enough to submit the skeleton and drive the rest of the pipeline
    # (see Pipeline.submit_syllabus, which falls back to subject_id when
    # no separate document_id is configured).
    subject_id = request.form.get("subject_id", "").strip()
    # New, optional: "auto" (default) generates + submits everything for
    # the whole curriculum (overviews, pages, FAQs, programs, flashcards,
    # quizzes). "review" is the page-by-page content-only workflow.
    mode = request.form.get("mode", "auto").strip().lower()
    if mode not in ("auto", "review"):
        mode = "auto"

    if not subject_id:
        flash("Subject ID is required to submit.", "error")
        return redirect(url_for("pipeline.review", run_id=run_id))

    skeleton = json.loads(skeleton_json_path.read_text())
    pipe = _load_pipeline(run_id)
    # Setting subject_id on the config up front makes prompt_for_subject_id()
    # a no-op (it only prompts when cfg.subject_id is falsy) -- this is how
    # we skip the CLI's interactive input() in a web context.
    pipe.cfg.subject_id = subject_id

    try:
        pipe.submit_syllabus(skeleton)
        document_id = pipe.cfg.document_id

        # Always record run metadata (used by the optional Asset Studio,
        # /pipeline/assets/..., to resolve subject_id for later requests).
        # Purely additive: writes a new file, doesn't change this route's
        # response either way.
        _save_run_meta(
            work_dir,
            mode=mode,
            document_id=document_id,
            subject_id=subject_id,
            technology_name=skeleton.get("technology_name", ""),
        )

        if mode == "review":
            return redirect(url_for("pipeline.pages_start", run_id=run_id))

        # --- automatic full-curriculum generation ---
        # Fetches the real subject tree via subject_id, then walks
        # pillar -> module -> chapter -> pages (in order), then that
        # chapter's FAQ/example/practice programs, then -- once every
        # chapter in a module is done -- that module's flashcards/quiz.
        # Repeats for every module and every pillar. See full_generation.py.
        summary = pipe.generate_and_submit_everything(skeleton)
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
        summary=summary,
    )


# =======================================================================
# Review Mode: page-by-page generate -> preview/edit -> approve -> upload.
# Entirely optional -- only reachable by choosing "Manual Review" on the
# review page above. Does not touch the "content"/"upload" stages the
# automatic flow's state.json uses, aside from also marking "content"
# done per page (harmless/expected: it means the page's content has, in
# fact, been generated -- a later automatic run would correctly skip
# regenerating it).
# =======================================================================


@pipeline_bp.get("/pages/<run_id>")
def pages_start(run_id: str):
    work_dir = work_dir_for(run_id)
    meta = _load_run_meta(work_dir)
    skeleton_json_path = work_dir / "skeleton.json"

    if meta.get("mode") != "review" or not skeleton_json_path.exists():
        flash("This run isn't in Review Mode.", "error")
        return redirect(url_for("pipeline.index"))

    skeleton = json.loads(skeleton_json_path.read_text())
    order = _page_order(skeleton)
    if not order:
        flash("No pages found in this skeleton.", "error")
        return redirect(url_for("pipeline.index"))

    pipe = _load_pipeline(run_id)
    for key, _title, _breadcrumb in order:
        if not _is_handled(pipe, key):
            return redirect(url_for("pipeline.page_edit", run_id=run_id, key=key))

    return redirect(url_for("pipeline.review_complete", run_id=run_id))


@pipeline_bp.get("/pages/<run_id>/complete")
def review_complete(run_id: str):
    work_dir = work_dir_for(run_id)
    meta = _load_run_meta(work_dir)
    if meta.get("mode") != "review":
        flash("This run isn't in Review Mode.", "error")
        return redirect(url_for("pipeline.index"))

    return render_template(
        "pipeline/result.html",
        run_id=run_id,
        technology_name=meta.get("technology_name", ""),
        success=True,
        document_id=meta.get("document_id", ""),
        subject_id=meta.get("subject_id", ""),
    )


@pipeline_bp.get("/pages/<run_id>/<key>")
def page_edit(run_id: str, key: str):
    work_dir = work_dir_for(run_id)
    meta = _load_run_meta(work_dir)
    skeleton_json_path = work_dir / "skeleton.json"

    if meta.get("mode") != "review" or not skeleton_json_path.exists():
        flash("This run isn't in Review Mode.", "error")
        return redirect(url_for("pipeline.index"))

    skeleton = json.loads(skeleton_json_path.read_text())
    order = _page_order(skeleton)
    order_map = {k: (title, breadcrumb) for k, title, breadcrumb in order}
    if key not in order_map:
        flash("That page doesn't exist in this run's skeleton.", "error")
        return redirect(url_for("pipeline.pages_start", run_id=run_id))

    title, breadcrumb = order_map[key]
    pipe = _load_pipeline(run_id)
    pipe.cfg.subject_id = meta.get("subject_id")

    try:
        content = _generate_or_load_page(pipe, key, title, breadcrumb)
    except Exception as exc:
        flash(f"Content generation failed for '{title}': {exc}", "error")
        content = {"title": title, "sections": []}

    keys_in_order = [k for k, _, _ in order]
    position = keys_in_order.index(key) + 1

    return render_template(
        "pipeline/page_review.html",
        run_id=run_id,
        run_key=key,
        title=title,
        breadcrumb=breadcrumb,
        content_json=json.dumps(content, indent=2),
        sections=content.get("sections", []),
        position=position,
        total=len(keys_in_order),
        already_handled=_is_handled(pipe, key),
    )


@pipeline_bp.post("/pages/<run_id>/<key>/regenerate")
def page_regenerate(run_id: str, key: str):
    work_dir = work_dir_for(run_id)
    out_path = work_dir / "rewritten" / f"{key}.json"
    if out_path.exists():
        out_path.unlink()
    return redirect(url_for("pipeline.page_edit", run_id=run_id, key=key))


@pipeline_bp.post("/pages/<run_id>/<key>/approve")
def page_approve(run_id: str, key: str):
    work_dir = work_dir_for(run_id)
    meta = _load_run_meta(work_dir)
    if meta.get("mode") != "review":
        flash("This run isn't in Review Mode.", "error")
        return redirect(url_for("pipeline.index"))

    raw = request.form.get("content_json", "")
    try:
        payload = json.loads(raw)
        assert isinstance(payload, dict) and "sections" in payload
    except Exception:
        flash("That content isn't valid JSON in the expected {title, sections} shape -- fix it and try again.", "error")
        return redirect(url_for("pipeline.page_edit", run_id=run_id, key=key))

    out_path = work_dir / "rewritten" / f"{key}.json"
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(json.dumps(payload, indent=2))

    pipe = _load_pipeline(run_id)
    pipe.cfg.subject_id = meta.get("subject_id")

    try:
        lookup = _page_lookup(pipe, work_dir)
        title_key = (payload.get("title") or "").strip().lower()
        page_id = lookup.get(title_key)
        if not page_id:
            raise RuntimeError(f'No matching page found in subject tree for title "{payload.get("title")}"')

        client = CurriculumClient(pipe.cfg, pipe.logger)
        # Same endpoint/payload contract the automatic uploader already
        # uses (POST /api/pages/<id>/content) -- unchanged.
        ok = client.post_page_content(page_id, payload)
        if not ok:
            raise RuntimeError("Upload rejected by the content API - see server log for details.")
    except Exception as exc:
        flash(f"Approved content saved locally, but upload failed: {exc}", "error")
        pipe.state.mark("review_upload", key, f"failed:{exc}")
        return redirect(url_for("pipeline.page_edit", run_id=run_id, key=key))

    pipe.state.mark("review_upload", key, "done")
    return redirect(url_for("pipeline.pages_start", run_id=run_id))


@pipeline_bp.post("/pages/<run_id>/<key>/skip")
def page_skip(run_id: str, key: str):
    pipe = _load_pipeline(run_id)
    pipe.state.mark("review_upload", key, "skipped")
    return redirect(url_for("pipeline.pages_start", run_id=run_id))
