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

    LLM_PROVIDER    which AI provider generates content: "anthropic"
                    (default), "openai", or "gemini". See
                    elluval_pipeline/llm_providers.py.
    ANTHROPIC_API_KEY / OPENAI_API_KEY / GEMINI_API_KEY
                    the API key for whichever provider LLM_PROVIDER
                    selects. Only the active provider's key is required.

Optional:
    WORK_DIR        working/output directory root (default ./work)
    PAGES_PER_CHAPTER, MODULES_PER_PILLAR  skeleton grouping tunables
    DOCUMENT_ID     target id for POST /api/documents/syllabus-import/<id>
                    (can also be passed directly to pipeline.run_ai)
    SKELETON_MODEL  model used to draft the curriculum outline (defaults
                    to the active provider's default model, see
                    llm_providers.DEFAULT_MODELS)
    CONTENT_MODEL   model used to write each page's content (defaults the
                    same way as SKELETON_MODEL)
    DEMO_MODE       "auto" (default) / "on" / "off" -- see demo_content.py.
                    In "auto", content generation automatically falls back
                    to realistic mock content whenever the active
                    provider's API key is missing/unset, and automatically
                    resumes calling the real API the moment a real key is
                    configured.
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

from . import demo_content
from .llm_providers import DEFAULT_MODELS, normalize_provider


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
    # "auto" (default): Demo Mode turns on automatically whenever no usable
    # API key is configured for the active provider, and turns back off
    # automatically the moment a real one is -- no code changes needed
    # either direction. Can be forced with DEMO_MODE=on / DEMO_MODE=off.
    # See demo_content.py.
    demo_mode: str = "auto"
    # --- Multi-provider AI settings ---
    # `provider` selects which of the three keys below is actually used;
    # the other two are simply ignored (they don't need to be blank).
    # See elluval_pipeline/llm_providers.py for the provider abstraction.
    provider: str = "anthropic"
    openai_api_key: str | None = None
    gemini_api_key: str | None = None
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

    # ---- Additional educational-asset endpoints (optional feature set) ----
    # Same base URL / auth headers as everything else; added without
    # touching any of the URL helpers above.
    def chapter_overview_url(self, chapter_id) -> str:
        return f"{self.base_url}/api/curriculum/chapters/{chapter_id}/overview?subjectId={self.subject_id}"

    def pillar_overview_url(self, pillar_id) -> str:
        return f"{self.base_url}/api/curriculum/pillars/{pillar_id}/overview?subjectId={self.subject_id}"

    def module_flashcards_url(self, module_id) -> str:
        return f"{self.base_url}/api/curriculum/modules/{module_id}/flashcards"

    def module_quiz_url(self, module_id) -> str:
        return f"{self.base_url}/api/curriculum/modules/{module_id}/quiz?subjectId={self.subject_id}"

    def compiler_practice_url(self, chapter_id) -> str:
        return f"{self.base_url}/api/compiler/practice/chapter/{chapter_id}"

    # ---- Multi-provider AI ------------------------------------------
    @property
    def active_api_key(self) -> str | None:
        """The API key for whichever provider `self.provider` selects."""
        return {
            "anthropic": self.anthropic_api_key,
            "openai": self.openai_api_key,
            "gemini": self.gemini_api_key,
        }.get(normalize_provider(self.provider))

    # ---- Demo Mode -------------------------------------------------
    @property
    def is_demo_mode(self) -> bool:
        """True when content-generation calls should use demo_content.py's
        mock generators instead of a real provider call. See
        demo_content.resolve_demo_mode() for the exact rules."""
        return demo_content.resolve_demo_mode(self.active_api_key, self.demo_mode)


def _read_cookie_file(path: Path) -> str:
    """Build the raw Cookie header value from API_COOKIE_FILE.

    Historically this file held one raw, already-`; `-joined cookie
    string (e.g. copied from browser devtools), possibly with a stray
    trailing newline -- hence the original newline-stripping behavior,
    preserved below for that case.

    The admin Cookies page (see elluval_pipeline/cookie_store.py) writes
    a more human-editable "NAME=VALUE" per line format instead. Detect
    that shape and join it into a valid Cookie header ("NAME=VALUE; ...")
    rather than concatenating it into garbage. Either format works
    interchangeably; nothing about existing single-line cookie files
    changes.
    """
    if not path.exists():
        return ""
    raw = path.read_text()
    lines = [ln.strip() for ln in raw.replace("\r", "").split("\n") if ln.strip()]
    if len(lines) > 1 and all("=" in ln for ln in lines):
        return "; ".join(lines)
    return raw.replace("\r", "").replace("\n", "").strip()


def load_config(subject_id: str | None = None) -> Config:
    cookie_path = Path(os.environ.get("API_COOKIE_FILE", "cookies.txt"))
    cookie = _read_cookie_file(cookie_path)

    work_dir = Path(os.environ.get("WORK_DIR", "./work")).resolve()
    work_dir.mkdir(parents=True, exist_ok=True)

    provider = normalize_provider(os.environ.get("LLM_PROVIDER", "anthropic"))
    provider_default_model = DEFAULT_MODELS[provider]

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
        # If unset, fall back to the active provider's default model
        # rather than a hardcoded Anthropic model name -- so switching
        # LLM_PROVIDER without also setting SKELETON_MODEL/CONTENT_MODEL
        # doesn't accidentally send an Anthropic model name to OpenAI/Gemini.
        skeleton_model=os.environ.get("SKELETON_MODEL") or provider_default_model,
        content_model=os.environ.get("CONTENT_MODEL") or provider_default_model,
        demo_mode=os.environ.get("DEMO_MODE", "auto"),
        provider=provider,
        openai_api_key=os.environ.get("OPENAI_API_KEY"),
        gemini_api_key=os.environ.get("GEMINI_API_KEY"),
    )
