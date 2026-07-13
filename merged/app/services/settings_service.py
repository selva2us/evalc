"""
settings_service.py
====================
Backs the admin "Environment Variables" management page
(/admin/settings).

Runtime-editable settings (Anthropic keys/models, curriculum backend
BASE_URL/API_TOKEN, and any future AI provider keys/config) are:

  1. Read from os.environ, same as always -- nothing about how existing
     code reads these values changes. `elluval_pipeline.config.load_config()`
     already re-reads os.environ on every call, and Flask routes read
     `current_app.config`, which this module keeps mirrored to os.environ.
  2. When updated from the admin UI, written to THREE places in the same
     request:
       a. os.environ[key]        -- so elluval_pipeline (which reads env
                                     vars fresh on every pipeline run) picks
                                     it up on its very next call.
       b. current_app.config[key] -- so Flask routes that read
                                     current_app.config.get(...) (the
                                     Architect tool) see it immediately too.
       c. the project's .env file, via python-dotenv's set_key() -- so the
                                     change survives a process restart.
  This gives the same "seamless switch, no code changes, no restart"
  guarantee Demo Mode already provides for a key configured before the
  process started -- now it also applies to a key added *while the app is
  running*.

Nothing here touches how ANTHROPIC_API_KEY/BASE_URL/etc. are *consumed* --
only how they're edited and persisted. The existing Anthropic integration,
elluval_pipeline, and every generation code path are unmodified.
"""
from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

from dotenv import set_key

# Project root .env (same file elluval_pipeline/config.py's load_dotenv()
# and python-dotenv's default search already load on process start).
ENV_FILE_PATH = Path(__file__).resolve().parents[2] / ".env"


@dataclass(frozen=True)
class SettingField:
    key: str
    label: str
    group: str
    secret: bool = False
    help_text: str = ""
    placeholder: str = ""


# The registry the admin UI renders from. Adding a future AI provider key
# or any other runtime config value is a one-line addition here -- nothing
# about the route, template, or persistence logic needs to change.
SETTINGS_FIELDS: list[SettingField] = [
    SettingField(
        "ANTHROPIC_API_KEY", "Anthropic API Key", "AI Provider", secret=True,
        help_text="Used by both the Architect tool and the AI Pipeline/Asset Studio. "
                   "Leave unset to run in Demo Mode (see the banner at the top of every page).",
        placeholder="sk-ant-...",
    ),
    SettingField(
        "ANTHROPIC_MODEL", "Anthropic Model (Architect tool)", "AI Provider",
        placeholder="claude-sonnet-5",
    ),
    SettingField(
        "SKELETON_MODEL", "Skeleton Model (AI Pipeline)", "AI Provider",
        placeholder="claude-sonnet-4-6",
    ),
    SettingField(
        "CONTENT_MODEL", "Content Model (AI Pipeline / Asset Studio)", "AI Provider",
        placeholder="claude-sonnet-4-6",
    ),
    SettingField(
        "DEMO_MODE", "Demo Mode", "AI Provider",
        help_text='"auto" (default), "on" (always demo), or "off" (never demo).',
        placeholder="auto",
    ),
    SettingField(
        "BASE_URL", "Curriculum Backend Base URL", "Curriculum Backend",
        help_text="Where skeletons/pages/assets get submitted (api_client.py / uploader.py).",
        placeholder="https://your-curriculum-backend.example.com",
    ),
    SettingField(
        "API_TOKEN", "Curriculum Backend API Token", "Curriculum Backend", secret=True,
    ),
]

_FIELDS_BY_KEY = {f.key: f for f in SETTINGS_FIELDS}


def mask_value(value: str | None, secret: bool) -> str:
    """Display-safe representation: never show a full secret value."""
    if not value:
        return "Not configured"
    if not secret:
        return value
    if len(value) <= 8:
        return "•" * len(value)
    return f"{value[:4]}{'•' * 8}{value[-4:]}"


def get_current_settings() -> list[dict]:
    """Everything the admin settings page needs to render: label, group,
    whether it's configured, and its masked display value."""
    rows = []
    for field in SETTINGS_FIELDS:
        raw = os.environ.get(field.key, "")
        rows.append({
            "key": field.key,
            "label": field.label,
            "group": field.group,
            "secret": field.secret,
            "help_text": field.help_text,
            "placeholder": field.placeholder,
            "configured": bool(raw),
            "masked_value": mask_value(raw, field.secret),
        })
    return rows


class SettingValidationError(ValueError):
    pass


def _validate(field: SettingField, value: str) -> str:
    value = value.strip()
    if "\n" in value or "\r" in value:
        raise SettingValidationError(f"{field.label} cannot contain newlines.")
    if field.key == "BASE_URL" and value and not (value.startswith("http://") or value.startswith("https://")):
        raise SettingValidationError("Base URL must start with http:// or https://")
    if field.key == "DEMO_MODE" and value and value.lower() not in ("auto", "on", "off"):
        raise SettingValidationError('Demo Mode must be "auto", "on", or "off".')
    return value


def update_setting(app, key: str, value: str) -> None:
    """Validate, then persist to os.environ + current_app.config + .env,
    all three, so the change is live immediately and survives a restart."""
    field = _FIELDS_BY_KEY.get(key)
    if field is None:
        raise SettingValidationError(f"Unknown setting: {key}")

    value = _validate(field, value)

    os.environ[key] = value
    app.config[key] = value

    try:
        ENV_FILE_PATH.touch(exist_ok=True)
        set_key(str(ENV_FILE_PATH), key, value, quote_mode="always")
    except OSError as exc:
        # Live value is already applied above even if the on-disk write
        # fails (e.g. read-only filesystem) -- surface a clear warning
        # rather than silently losing the change on next restart.
        raise SettingValidationError(
            f"Setting applied for this session, but could not be written to "
            f"{ENV_FILE_PATH} ({exc}). It will not survive a restart until "
            f"that's fixed."
        ) from exc


def clear_setting(app, key: str) -> None:
    """Unset a value everywhere (env, Flask config, .env file)."""
    field = _FIELDS_BY_KEY.get(key)
    if field is None:
        raise SettingValidationError(f"Unknown setting: {key}")
    os.environ.pop(key, None)
    app.config[key] = ""
    try:
        ENV_FILE_PATH.touch(exist_ok=True)
        set_key(str(ENV_FILE_PATH), key, "", quote_mode="always")
    except OSError:
        pass
