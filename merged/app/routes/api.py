from flask import Blueprint, jsonify, request

from app.extensions import db
from app.models import Curriculum
from app.routes.main import _model_used_label
from app.services.llm_service import LLMServiceError, generate_curriculum

api_bp = Blueprint("api", __name__)


@api_bp.post("/generate")
def api_generate():
    payload = request.get_json(silent=True) or {}
    technology_name = str(payload.get("technology_name", "")).strip()

    if not technology_name:
        return jsonify({"error": "technology_name is required"}), 400

    try:
        markdown = generate_curriculum(technology_name)
    except LLMServiceError as exc:
        return jsonify({"error": str(exc)}), 502

    record = Curriculum(
        technology_name=technology_name,
        markdown=markdown,
        model_used=_model_used_label(),
    )
    db.session.add(record)
    db.session.commit()

    return jsonify(record.to_dict()), 201


@api_bp.get("/curricula")
def api_list_curricula():
    limit = min(int(request.args.get("limit", 20)), 100)
    records = (
        Curriculum.query.order_by(Curriculum.created_at.desc()).limit(limit).all()
    )
    return jsonify([r.to_dict() for r in records])


@api_bp.get("/curricula/<curriculum_id>")
def api_get_curriculum(curriculum_id: str):
    record = Curriculum.query.get(curriculum_id)
    if record is None:
        return jsonify({"error": "not found"}), 404
    return jsonify(record.to_dict())
