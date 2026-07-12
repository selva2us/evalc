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

main_bp = Blueprint("main", __name__)


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
        model_used=current_app.config.get("ANTHROPIC_MODEL", "unknown"),
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
