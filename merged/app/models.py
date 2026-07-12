import uuid
from datetime import datetime, timezone

from app.extensions import db


def _uuid() -> str:
    return str(uuid.uuid4())


class Curriculum(db.Model):
    """
    A single generated curriculum skeleton.

    Storing these (rather than only returning them) is what makes future
    features cheap to add later: history view, search, re-download,
    regeneration, versioning, sharing links, LMS export jobs, etc.
    """

    __tablename__ = "curricula"

    id = db.Column(db.String(36), primary_key=True, default=_uuid)
    technology_name = db.Column(db.String(120), nullable=False, index=True)
    markdown = db.Column(db.Text, nullable=False)
    model_used = db.Column(db.String(120), nullable=False)
    created_at = db.Column(
        db.DateTime, default=lambda: datetime.now(timezone.utc), nullable=False
    )

    def to_dict(self) -> dict:
        return {
            "id": self.id,
            "technology_name": self.technology_name,
            "markdown": self.markdown,
            "model_used": self.model_used,
            "created_at": self.created_at.isoformat(),
        }
