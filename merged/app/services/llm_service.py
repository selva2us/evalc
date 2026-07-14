"""
Curriculum generation service.

All LLM API interaction lives here, isolated from Flask routes, via the
provider-agnostic elluval_pipeline.llm_providers module. This makes it easy
to swap models, add streaming, add caching, add retry/backoff, add a
queue/worker, or add a fourth provider without touching route code.
"""
from __future__ import annotations

from flask import current_app

from elluval_pipeline.demo_content import generate_demo_skeleton_markdown, resolve_demo_mode
from elluval_pipeline.llm_providers import LLMProviderError, complete, normalize_provider
from elluval_pipeline.prompts import get_prompt


class LLMServiceError(RuntimeError):
    """Raised when the curriculum could not be generated."""


def build_prompt(technology_name: str) -> str:
    # Prompt text lives in prompts/curriculum_system_prompt.txt (see
    # elluval_pipeline/prompts.py) rather than hardcoded here.
    return get_prompt("curriculum_system_prompt", technology_name=technology_name.strip())


def _active_provider_settings() -> tuple[str, str | None, str | None]:
    """Returns (provider, api_key, model) for whichever provider
    LLM_PROVIDER currently selects, reading straight from current_app.config
    (which app/services/settings_service.py keeps mirrored to os.environ)."""
    provider = normalize_provider(current_app.config.get("LLM_PROVIDER", "anthropic"))
    api_key_config_key = {
        "anthropic": "ANTHROPIC_API_KEY",
        "openai": "OPENAI_API_KEY",
        "gemini": "GEMINI_API_KEY",
    }[provider]
    model_config_key = {
        "anthropic": "ANTHROPIC_MODEL",
        "openai": "OPENAI_MODEL",
        "gemini": "GEMINI_MODEL",
    }[provider]
    api_key = current_app.config.get(api_key_config_key)
    model = current_app.config.get(model_config_key)
    return provider, api_key, model


def generate_curriculum(technology_name: str) -> str:
    """
    Calls the active provider (Anthropic, OpenAI, or Gemini -- see
    LLM_PROVIDER) and returns the raw markdown curriculum.

    Raises LLMServiceError on any failure (missing key, API error, empty
    response) so calling routes can turn it into a clean HTTP response.
    """
    provider, api_key, model = _active_provider_settings()
    demo_mode_setting = current_app.config.get("DEMO_MODE", "auto")

    # Demo Mode: no usable API key for the active provider (or DEMO_MODE
    # forced on) -> serve a deterministic mock curriculum instead of
    # failing. Reverts to the real call automatically once a real key is
    # configured.
    if resolve_demo_mode(api_key, demo_mode_setting):
        return generate_demo_skeleton_markdown(technology_name)

    max_tokens = current_app.config.get("ANTHROPIC_MAX_TOKENS", 8000)
    prompt = build_prompt(technology_name)

    try:
        markdown = complete(provider, api_key, model, user=prompt, max_tokens=max_tokens)
    except LLMProviderError as exc:
        raise LLMServiceError(str(exc)) from exc

    # Strip accidental code fences if the model wraps the output anyway.
    if markdown.startswith("```"):
        lines = markdown.splitlines()
        if lines[0].startswith("```"):
            lines = lines[1:]
        if lines and lines[-1].strip().startswith("```"):
            lines = lines[:-1]
        markdown = "\n".join(lines).strip()

    return markdown
