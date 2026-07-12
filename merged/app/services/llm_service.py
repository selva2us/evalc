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


class LLMServiceError(RuntimeError):
    """Raised when the curriculum could not be generated."""


PROMPT_TEMPLATE = """You are a world-class curriculum architect, senior software engineer, technical author, and educational content designer.
Your task is to generate a COMPLETE markdown learning skeleton for the technology provided by the user.
Technology:
{technology_name}
The output must be a clean and structured markdown document.
The markdown must follow this hierarchy exactly:
# Pillar
## Module
### Chapter
Page
Example:
# Pillar 1 – Foundations
## Module 1 – Introduction
### Chapter 1 – History
Page 1 - Origins
Page 2 - Evolution
Page 3 - Major Milestones
Rules:
1. Generate between 8 and 15 Pillars.
2. Each Pillar should contain 4 to 10 Modules.
3. Each Module should contain 5 to 12 Chapters.
4. Each Chapter should contain 5 to 20 Pages.
5. The structure must progress naturally from beginner to expert level.
The curriculum must include:
- History and evolution
- Core concepts
- Syntax and fundamentals
- Internal architecture
- Runtime behavior
- Memory management
- Design patterns
- Ecosystem and libraries
- Tooling
- Security
- Performance
- Debugging
- Testing
- Deployment
- Best practices
- Real-world applications
- Enterprise usage
- Advanced topics
- Common mistakes
- Interview preparation
- Future roadmap
Technology-specific sections must be included, appropriate to {technology_name} (for example internals, runtime, concurrency model, ecosystem tools, and architecture concepts that are specific to this technology).
Requirements:
- Avoid generic tutorials.
- Do not generate content explanations.
- Generate only the curriculum structure.
- Ensure every topic appears exactly once.
- Avoid duplicate chapters.
- Ensure logical learning progression.
- Prefer industry standards over academic ordering.
- Include internals and architecture wherever applicable.
- Include historical context where relevant.
- Include deprecated technologies if they influenced modern design.
- Include ecosystem tools and alternatives.
- Include production and enterprise usage patterns.
Formatting rules:
- Output must be valid markdown.
- Use only markdown headings and bullet-free page lines.
- Do not include introductory text.
- Do not include conclusions.
- Do not include explanations outside the hierarchy.
- Use title case for all headings.
- Keep naming concise and professional.
The generated markdown should be suitable for:
- PDF generation
- LMS systems
- Curriculum management systems
- Course generation pipeline
- AI content generation pipelines.
Respond with ONLY the markdown document. No preamble, no code fences, no commentary.
"""


def build_prompt(technology_name: str) -> str:
    return PROMPT_TEMPLATE.format(technology_name=technology_name.strip())


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
