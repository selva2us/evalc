from flask import (
    Blueprint,
    Response,
    current_app,
    flash,
    redirect,
    render_template,
    request,
    url_for,
)

from app.extensions import db
from app.models import Curriculum
from app.services.llm_service import LLMServiceError, generate_curriculum
from elluval_pipeline.demo_content import resolve_demo_mode

main_bp = Blueprint("main", __name__)


def _model_used_label() -> str:
    """What to record/display as the 'model' for a generated curriculum --
    the real configured model, or a clear "demo-mode" label when Demo Mode
    served the content instead (see app/services/llm_service.py)."""
    api_key = current_app.config.get("ANTHROPIC_API_KEY")
    demo_setting = current_app.config.get("DEMO_MODE", "auto")
    if resolve_demo_mode(api_key, demo_setting):
        return "demo-mode (sample content)"
    return current_app.config.get("ANTHROPIC_MODEL", "unknown")


@main_bp.get("/")
def index():
    return render_template("index.html")


@main_bp.post("/generate")
def generate():
    technology_name = request.form.get("technology_name", "").strip()

    if not technology_name:
        flash("Please enter a technology name.", "error")
        return redirect(url_for("main.index"))

    try:
        markdown = generate_curriculum(technology_name)
    except LLMServiceError as exc:
        flash(str(exc), "error")
        return redirect(url_for("main.index"))

    record = Curriculum(
        technology_name=technology_name,
        markdown=markdown,
        model_used=_model_used_label(),
    )
    db.session.add(record)
    db.session.commit()

    return redirect(url_for("main.result", curriculum_id=record.id))


@main_bp.get("/result/<curriculum_id>")
def result(curriculum_id: str):
    record = Curriculum.query.get_or_404(curriculum_id)
    return render_template("result.html", record=record)


@main_bp.get("/result/<curriculum_id>/download")
def download(curriculum_id: str):
    record = Curriculum.query.get_or_404(curriculum_id)
    filename = f"{record.technology_name.lower().replace(' ', '-')}-curriculum.md"
    return Response(
        record.markdown,
        mimetype="text/markdown",
        headers={"Content-Disposition": f"attachment; filename={filename}"},
    )


@main_bp.get("/history")
def history():
    records = Curriculum.query.order_by(Curriculum.created_at.desc()).limit(50).all()
    return render_template("history.html", records=records)
