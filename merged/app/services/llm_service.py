"""
Curriculum generation service.

All Claude/Anthropic API interaction lives here, isolated from Flask routes.
This makes it easy to later: swap models, add streaming, add caching, add
retry/backoff, add a queue/worker, or support multiple LLM providers side
by side without touching route code.
"""
from __future__ import annotations

import anthropic
from flask import current_app

from elluval_pipeline.demo_content import generate_demo_skeleton_markdown, resolve_demo_mode
from elluval_pipeline.prompts import get_prompt


class LLMServiceError(RuntimeError):
    """Raised when the curriculum could not be generated."""


def build_prompt(technology_name: str) -> str:
    # Prompt text lives in prompts/curriculum_system_prompt.txt (see
    # elluval_pipeline/prompts.py) rather than hardcoded here.
    return get_prompt("curriculum_system_prompt", technology_name=technology_name.strip())


def generate_curriculum(technology_name: str) -> str:
    """
    Calls the Anthropic API and returns the raw markdown curriculum.

    Raises LLMServiceError on any failure (missing key, API error, empty
    response) so calling routes can turn it into a clean HTTP response.
    """
    api_key = current_app.config.get("ANTHROPIC_API_KEY")
    demo_mode_setting = current_app.config.get("DEMO_MODE", "auto")

    # Demo Mode: no usable ANTHROPIC_API_KEY (or DEMO_MODE forced on) ->
    # serve a deterministic mock curriculum instead of failing. Reverts to
    # the real Anthropic call automatically once a real key is configured.
    if resolve_demo_mode(api_key, demo_mode_setting):
        return generate_demo_skeleton_markdown(technology_name)

    if not api_key:
        raise LLMServiceError(
            "ANTHROPIC_API_KEY is not configured. Set it in your environment "
            "or .env file before generating curricula."
        )

    model = current_app.config.get("ANTHROPIC_MODEL", "claude-sonnet-5")
    max_tokens = current_app.config.get("ANTHROPIC_MAX_TOKENS", 8000)

    client = anthropic.Anthropic(api_key=api_key)
    prompt = build_prompt(technology_name)

    try:
        response = client.messages.create(
            model=model,
            max_tokens=max_tokens,
            messages=[{"role": "user", "content": prompt}],
        )
    except anthropic.APIError as exc:
        raise LLMServiceError(f"Anthropic API error: {exc}") from exc

    text_parts = [
        block.text for block in response.content if getattr(block, "type", "") == "text"
    ]
    markdown = "\n".join(text_parts).strip()

    if not markdown:
        raise LLMServiceError("Received an empty response from the model.")

    # Strip accidental code fences if the model wraps the output anyway.
    if markdown.startswith("```"):
        lines = markdown.splitlines()
        if lines[0].startswith("```"):
            lines = lines[1:]
        if lines and lines[-1].strip().startswith("```"):
            lines = lines[:-1]
        markdown = "\n".join(lines).strip()

    return markdown
