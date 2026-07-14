"""
llm_providers.py
================
A thin, provider-agnostic wrapper around chat-completion calls to
Anthropic (Claude), OpenAI, and Google Gemini.

Every place in this codebase that used to do:

    from anthropic import Anthropic
    client = Anthropic(api_key=cfg.anthropic_api_key)
    resp = client.messages.create(model=..., max_tokens=..., messages=[...])
    text = "".join(b.text for b in resp.content if b.type == "text")

now instead does:

    from elluval_pipeline.llm_providers import complete
    text = complete(cfg.provider, cfg.active_api_key, model,
                     system=system_prompt, user=user_prompt, max_tokens=...)

`complete()` returns a plain string (the model's text response) regardless
of which provider served it, and raises `LLMProviderError` on any failure
(missing/invalid key, network error, provider-side error) so callers can
keep a single except clause instead of branching on
`anthropic.APIError` / `openai.OpenAIError` / etc.

Adding a fourth provider later means adding one `_complete_<provider>`
function below and one entry in `SUPPORTED_PROVIDERS` / `DEFAULT_MODELS` --
nothing about the call sites needs to change.
"""
from __future__ import annotations

SUPPORTED_PROVIDERS = ("anthropic", "openai", "gemini")

# Sensible out-of-the-box default model per provider. Any of these can be
# overridden per use-case (Architect tool / Skeleton stage / Content stage)
# via the admin Settings page or the matching environment variable -- see
# app/services/settings_service.py.
DEFAULT_MODELS = {
    "anthropic": "claude-sonnet-5",
    "openai": "gpt-5.1",
    "gemini": "gemini-3.5-flash",
}

# Which environment variable / settings-page field holds each provider's key.
API_KEY_ENV_VARS = {
    "anthropic": "ANTHROPIC_API_KEY",
    "openai": "OPENAI_API_KEY",
    "gemini": "GEMINI_API_KEY",
}

PROVIDER_LABELS = {
    "anthropic": "Anthropic (Claude)",
    "openai": "OpenAI (GPT)",
    "gemini": "Google (Gemini)",
}


class LLMProviderError(RuntimeError):
    """Raised for any provider call failure: missing key, network error,
    provider-side error, or an unrecognized provider name."""


def normalize_provider(name: str | None) -> str:
    name = (name or "anthropic").strip().lower()
    if name not in SUPPORTED_PROVIDERS:
        raise LLMProviderError(
            f"Unknown LLM provider: {name!r}. Supported providers: "
            f"{', '.join(SUPPORTED_PROVIDERS)}."
        )
    return name


def default_model_for(provider: str | None) -> str:
    return DEFAULT_MODELS[normalize_provider(provider)]


def complete(
    provider: str | None,
    api_key: str | None,
    model: str | None,
    *,
    user: str,
    system: str | None = None,
    max_tokens: int = 4000,
) -> str:
    """Send one user turn (with an optional system prompt) to the given
    provider and return the model's text response, stripped of leading/
    trailing whitespace. Raises LLMProviderError on any failure, including
    a missing/blank api_key."""
    provider = normalize_provider(provider)

    if not api_key or not api_key.strip():
        raise LLMProviderError(
            f"{API_KEY_ENV_VARS[provider]} is not configured. Set it in the "
            f"admin Settings page or your environment/.env file."
        )

    model = model or default_model_for(provider)

    if provider == "anthropic":
        return _complete_anthropic(api_key, model, system, user, max_tokens)
    if provider == "openai":
        return _complete_openai(api_key, model, system, user, max_tokens)
    return _complete_gemini(api_key, model, system, user, max_tokens)


# ---------------------------------------------------------------------
# Anthropic (Claude)
# ---------------------------------------------------------------------
def _complete_anthropic(api_key: str, model: str, system: str | None, user: str, max_tokens: int) -> str:
    import anthropic

    client = anthropic.Anthropic(api_key=api_key)
    kwargs = {"system": system} if system else {}
    try:
        resp = client.messages.create(
            model=model,
            max_tokens=max_tokens,
            messages=[{"role": "user", "content": user}],
            **kwargs,
        )
    except anthropic.APIError as exc:
        raise LLMProviderError(f"Anthropic API error: {exc}") from exc

    text = "".join(block.text for block in resp.content if getattr(block, "type", "") == "text").strip()
    if not text:
        raise LLMProviderError("Received an empty response from Anthropic.")
    return text


# ---------------------------------------------------------------------
# OpenAI (GPT)
# ---------------------------------------------------------------------
def _complete_openai(api_key: str, model: str, system: str | None, user: str, max_tokens: int) -> str:
    from openai import OpenAI

    client = OpenAI(api_key=api_key)
    messages = []
    if system:
        messages.append({"role": "system", "content": system})
    messages.append({"role": "user", "content": user})

    # Newer OpenAI models (the o-series, gpt-5.x) reject the legacy
    # `max_tokens` param in favor of `max_completion_tokens`; older ones
    # only understand `max_tokens`. Try the modern name first and fall
    # back rather than guessing from the model string.
    last_exc: Exception | None = None
    for token_param in ("max_completion_tokens", "max_tokens"):
        try:
            resp = client.chat.completions.create(
                model=model, messages=messages, **{token_param: max_tokens}
            )
            break
        except Exception as exc:  # noqa: BLE001 - retried below, re-raised after
            last_exc = exc
            resp = None
    else:
        resp = None

    if resp is None:
        raise LLMProviderError(f"OpenAI API error: {last_exc}") from last_exc

    text = (resp.choices[0].message.content or "").strip()
    if not text:
        raise LLMProviderError("Received an empty response from OpenAI.")
    return text


# ---------------------------------------------------------------------
# Google Gemini
# ---------------------------------------------------------------------
def _complete_gemini(api_key: str, model: str, system: str | None, user: str, max_tokens: int) -> str:
    from google import genai
    from google.genai import types

    client = genai.Client(api_key=api_key)

    # Gemini 2.5 (and later) models "think" before answering, and by
    # default the thinking tokens are drawn from the SAME max_output_tokens
    # budget as the visible answer. On a small budget (e.g. the 2000-token
    # content-generation calls) the model can spend its *entire* allowance
    # thinking and return literally zero answer text -- which used to
    # surface here as a generic, confusing "empty response" error. Capping
    # the thinking budget leaves headroom for the actual answer.
    # thinking_budget requires google-genai>=1.x; see requirements.txt.
    thinking_budget = min(1024, max(0, max_tokens // 4))
    config_kwargs = {
        "max_output_tokens": max_tokens,
        "thinking_config": types.ThinkingConfig(thinking_budget=thinking_budget),
    }
    if system:
        config_kwargs["system_instruction"] = system

    try:
        resp = client.models.generate_content(
            model=model,
            contents=user,
            config=types.GenerateContentConfig(**config_kwargs),
        )
    except TypeError:
        # Older google-genai versions (<1.x) don't know about
        # thinking_config at all -- retry without it rather than hard-fail,
        # though upgrading the package is the real fix (see requirements.txt).
        config_kwargs.pop("thinking_config", None)
        try:
            resp = client.models.generate_content(
                model=model,
                contents=user,
                config=types.GenerateContentConfig(**config_kwargs),
            )
        except Exception as exc:  # noqa: BLE001 - google-genai raises several error types
            raise LLMProviderError(f"Gemini API error: {exc}") from exc
    except Exception as exc:  # noqa: BLE001 - google-genai raises several error types
        raise LLMProviderError(f"Gemini API error: {exc}") from exc

    text = (getattr(resp, "text", None) or "").strip()
    if not text:
        # Give a diagnosable reason instead of a bare "empty response":
        # blocked-by-safety-filters, truncated by MAX_TOKENS, etc. all look
        # identical from resp.text alone.
        finish_reason = None
        candidates = getattr(resp, "candidates", None) or []
        if candidates:
            finish_reason = getattr(candidates[0], "finish_reason", None)
        detail = f" (finish_reason={finish_reason})" if finish_reason else ""
        raise LLMProviderError(
            f"Received an empty response from Gemini{detail}. This usually means "
            f"the model spent its whole token budget on internal reasoning or hit "
            f"a safety filter -- try raising max_tokens or simplifying the prompt."
        )
    return text
