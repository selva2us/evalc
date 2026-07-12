"""
config.py
=========
All credentials and environment-specific settings live here, sourced from
environment variables (optionally loaded from a local .env file). Nothing
sensitive is hardcoded — the original Ruby scripts had a live bearer token
and session cookie committed directly in source, which is a real security
risk (anyone with repo/chat access could hit the production API). Don't
repeat that pattern: keep secrets in `.env` (gitignored) or your shell env.

Required environment variables:
    BASE_URL        e.g. https://dev.elluval.com
    SUBJECT_ID      numeric subject id (can also be entered interactively
                    at the pipeline pause-point, see pipeline.py)
    API_TOKEN       bearer token for the curriculum API
    API_COOKIE_FILE path to a file containing the session cookie string
                    (defaults to ./cookies.txt, same convention as before)
    ANTHROPIC_API_KEY  used by rewriter.py to regenerate content

Optional:
    WORK_DIR        working/output directory root (default ./work)
    PAGES_PER_CHAPTER, MODULES_PER_PILLAR  skeleton grouping tunables
    DOCUMENT_ID     target id for POST /api/documents/syllabus-import/<id>
                    (can also be passed directly to pipeline.run_ai)
    SKELETON_MODEL  model used to draft the curriculum outline (default
                    claude-sonnet-4-6)
    CONTENT_MODEL   model used to write each page's content (default
                    claude-sonnet-4-6)
"""
from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path

try:
    from dotenv import load_dotenv  # optional convenience, pip install python-dotenv
    load_dotenv()
except ImportError:
    pass


def _require(name: str, default: str | None = None) -> str:
    val = os.environ.get(name, default)
    if val is None:
        raise RuntimeError(
            f"Missing required environment variable: {name}. "
            f"Set it in your shell or a .env file before running the pipeline."
        )
    return val


@dataclass
class Config:
    base_url: str
    subject_id: str | None
    api_token: str
    cookie: str
    anthropic_api_key: str | None
    work_dir: Path
    pages_per_chapter: int = 6
    modules_per_pillar: int = 3
    document_id: str | None = None
    skeleton_model: str = "claude-sonnet-4-6"
    content_model: str = "claude-sonnet-4-6"
    headers: dict = field(init=False)
    upload_headers: dict = field(init=False)

    def __post_init__(self):
        self.headers = {
            "Authorization": f"Bearer {self.api_token}",
            "Cookie": self.cookie,
            "Content-Type": "application/json",
            "Accept": "application/json",
        }
        self.upload_headers = {
            "Authorization": f"Bearer {self.api_token}",
            "Cookie": self.cookie,
            "Accept": "application/json",
        }

    @property
    def tree_url(self) -> str:
        return f"{self.base_url}/api/subjects/{self.subject_id}/tree"

    def page_content_url(self, page_id) -> str:
        return f"{self.base_url}/api/pages/{page_id}/content"

    @property
    def upload_image_url(self) -> str:
        return f"{self.base_url}/api/pages/upload-image"

    def module_overview_url(self, module_id) -> str:
        return f"{self.base_url}/api/curriculum/modules/{module_id}/overview?subjectId={self.subject_id}"

    def syllabus_import_url(self, document_id) -> str:
        return f"{self.base_url}/api/documents/syllabus-import/{document_id}"


def load_config(subject_id: str | None = None) -> Config:
    cookie_path = Path(os.environ.get("API_COOKIE_FILE", "cookies.txt"))
    cookie = ""
    if cookie_path.exists():
        cookie = cookie_path.read_text().replace("\r", "").replace("\n", "").strip()

    work_dir = Path(os.environ.get("WORK_DIR", "./work")).resolve()
    work_dir.mkdir(parents=True, exist_ok=True)

    return Config(
        base_url=_require("BASE_URL", "https://dev.elluval.com"),
        subject_id=subject_id or os.environ.get("SUBJECT_ID"),
        api_token=_require("API_TOKEN"),
        cookie=cookie,
        anthropic_api_key=os.environ.get("ANTHROPIC_API_KEY"),
        work_dir=work_dir,
        pages_per_chapter=int(os.environ.get("PAGES_PER_CHAPTER", 6)),
        modules_per_pillar=int(os.environ.get("MODULES_PER_PILLAR", 3)),
        document_id=os.environ.get("DOCUMENT_ID"),
        skeleton_model=os.environ.get("SKELETON_MODEL", "claude-sonnet-4-6"),
        content_model=os.environ.get("CONTENT_MODEL", "claude-sonnet-4-6"),
    )
