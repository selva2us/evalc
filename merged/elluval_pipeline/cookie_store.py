"""
cookie_store.py
================
Backs the admin "Cookies" management page (/admin/cookies) and feeds
elluval_pipeline.config.load_config()'s existing Cookie-header mechanism.

Some curriculum backends sit behind Cloudflare Access / Cloudflare Bot
Management, which requires specific cookies (CF_AppSession,
CF_Authorization, cf_clearance) on every request or the backend rejects
the call before it reaches the actual API. The pipeline already reads a
cookie value from API_COOKIE_FILE (default ./cookies.txt) and sends it as
the Cookie header on every request (see Config.headers in config.py) --
that part is pre-existing and untouched. This module is the admin-facing
way to manage *which* cookies are in that file: it stores each named
cookie server-side, regenerates cookies.txt from them (one NAME=VALUE per
line, which config.py's cookie-file reader now joins into a valid Cookie
header -- see _read_cookie_file() in config.py), and exposes per-cookie
status to the admin UI. With nothing configured, cookies.txt is simply
absent/empty, identical to the app's behavior before this feature existed.

Deliberately framework-agnostic (no Flask import): the file it writes is
consumed by elluval_pipeline.config.load_config(), which is also used
from the plain CLI entry point (cli_ai.py) with no Flask app/request
context available.

Storage: instance/cookies.json holds the structured, human-readable
metadata (value + last-updated timestamp) that powers the admin UI's
status display; instance/cookies.json itself is never rendered back to
the browser in full -- only whether each cookie is configured and when
it was last updated. cookies.txt (the file the pipeline actually reads)
is regenerated from it on every change. Both files get owner-only (0600)
permissions where the OS supports it.
"""
from __future__ import annotations

import json
import os
import stat
from datetime import datetime, timezone
from pathlib import Path

SUPPORTED_COOKIES = ["CF_AppSession", "CF_Authorization", "cf_clearance"]

INSTANCE_DIR = Path(__file__).resolve().parent.parent / "instance"
STORE_PATH = INSTANCE_DIR / "cookies.json"


def _cookies_txt_path() -> Path:
    # Same env var / default elluval_pipeline.config.load_config() reads,
    # resolved at call time (not import time) so an admin-updated
    # API_COOKIE_FILE setting takes effect on the very next save.
    return Path(os.environ.get("API_COOKIE_FILE", "cookies.txt"))


def _lock_down(path: Path) -> None:
    try:
        os.chmod(path, stat.S_IRUSR | stat.S_IWUSR)  # 0600: owner read/write only
    except OSError:
        pass  # best-effort; not all filesystems/platforms support chmod


def _load() -> dict:
    if not STORE_PATH.exists():
        return {}
    try:
        return json.loads(STORE_PATH.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return {}


def _write_cookies_txt(data: dict) -> None:
    """One NAME=VALUE per line -- human-readable/editable, and correctly
    parsed into a single Cookie header by config.py's _read_cookie_file()
    (joined with '; ' when the file has multiple such lines)."""
    path = _cookies_txt_path()
    lines = [f"{name}={info['value']}" for name, info in data.items() if info.get("value")]
    if not lines:
        # Nothing configured: remove the file rather than leaving an
        # empty one, so load_config() sees "no cookie file" exactly as
        # it did before this feature existed.
        if path.exists():
            path.unlink()
        return
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    _lock_down(path)


def _save(data: dict) -> None:
    INSTANCE_DIR.mkdir(parents=True, exist_ok=True)
    STORE_PATH.write_text(json.dumps(data, indent=2), encoding="utf-8")
    _lock_down(STORE_PATH)
    _write_cookies_txt(data)


def get_status() -> list[dict]:
    """Non-sensitive summary for the admin UI: name, whether it's set,
    and when it was last updated -- never the value itself."""
    data = _load()
    rows = []
    for name in SUPPORTED_COOKIES:
        info = data.get(name)
        rows.append({
            "name": name,
            "configured": bool(info and info.get("value")),
            "updated_at": info.get("updated_at") if info else None,
        })
    return rows


def set_cookie(name: str, value: str) -> None:
    if name not in SUPPORTED_COOKIES:
        raise ValueError(f"Unsupported cookie: {name}")
    value = value.strip()
    if not value:
        raise ValueError("Cookie value cannot be empty.")
    if "\n" in value or "\r" in value or ";" in value:
        raise ValueError("Cookie value cannot contain newlines or semicolons.")
    data = _load()
    data[name] = {"value": value, "updated_at": datetime.now(timezone.utc).isoformat()}
    _save(data)


def delete_cookie(name: str) -> None:
    data = _load()
    if name in data:
        del data[name]
        _save(data)


def clear_all() -> None:
    _save({})


def get_cookie_dict() -> dict:
    """{name: value} for every configured cookie. Not used by
    api_client.py directly (the pipeline already consumes cookies.txt via
    config.py's Cookie header) -- kept for completeness/introspection,
    e.g. by the admin UI or tests."""
    data = _load()
    return {name: info["value"] for name, info in data.items() if info.get("value")}

