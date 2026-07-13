"""
prompts.py
==========
Centralized system-prompt loader for every Anthropic call in the suite.

All prompt text lives as plain-text files under the top-level `prompts/`
directory (one file per prompt) instead of being hardcoded inside
services, routes, or pipeline files. This is a pure relocation: every
prompt's exact wording is unchanged from before, so no generation
behavior changes for existing users -- swap out a .txt file's contents
and every call site that uses it (web app, CLI, demo-mode detection,
everything) picks up the new wording immediately (or after clear_cache(),
see below), with zero code changes.

Usage:
    from elluval_pipeline.prompts import get_prompt

    system = get_prompt("content_generation_prompt")                     # static
    system = get_prompt("skeleton_prompt", technology_name="Kubernetes", # dynamic
                         notes_block="")

Dynamic prompts use $identifier / ${identifier} placeholders (Python's
string.Template, via safe_substitute) rather than str.format(). Several
prompts instruct the model to "respond in this exact JSON shape: {...}"
and contain literal { } braces -- str.format() would choke on those or
require doubling every brace, which makes the files unreadable/unsafe for
non-developers to edit. Template's $-syntax ignores plain { } entirely.
safe_substitute() also means a prompt file that's missing/renamed a
placeholder degrades gracefully (leaves $placeholder visibly in the text)
instead of raising, which matters once these files are meant to be
editable by non-developers.

Prompts are read from disk once and cached in-process (they're requested
on every single generation call, so re-reading the file each time would
be wasteful). Call clear_cache() after editing a .txt file on disk to
pick up the change without restarting the process.
"""
from __future__ import annotations

from pathlib import Path
from string import Template
from threading import Lock

PROMPTS_DIR = Path(__file__).resolve().parent.parent / "prompts"

_cache: dict[str, str] = {}
_cache_lock = Lock()


class PromptNotFoundError(RuntimeError):
    """Raised when prompts/<name>.txt doesn't exist."""


def _read(name: str) -> str:
    with _cache_lock:
        if name in _cache:
            return _cache[name]
    path = PROMPTS_DIR / f"{name}.txt"
    if not path.exists():
        raise PromptNotFoundError(
            f"Prompt file not found: {path}. Every system prompt is expected "
            f"to live in the top-level prompts/ directory -- see README.md."
        )
    text = path.read_text(encoding="utf-8")
    with _cache_lock:
        _cache[name] = text
    return text


def get_prompt(name: str, **kwargs) -> str:
    """Load prompts/<name>.txt (cached) and substitute any $identifier
    placeholders found in it. Safe to call with no kwargs for prompts
    that have none."""
    text = _read(name)
    return Template(text).safe_substitute(**kwargs)


def list_prompts() -> list[str]:
    """Names (without .txt) of every prompt file currently on disk --
    used by the (future) admin prompt-management UI."""
    if not PROMPTS_DIR.exists():
        return []
    return sorted(p.stem for p in PROMPTS_DIR.glob("*.txt"))


def clear_cache() -> None:
    """Drop the in-memory prompt cache so edited files are picked up
    without restarting the process."""
    with _cache_lock:
        _cache.clear()
