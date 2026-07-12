"""
demo_content.py
================
Deterministic, template-based "mock AI" content generator.

This is the single implementation of Demo Mode used across the whole
suite (the Architect tool's `app/services/llm_service.py`, and the AI
Pipeline's `ai_skeleton.py` / `ai_content.py` / `asset_generation.py`).
It never imports `anthropic` and never touches the network.

Why this exists
----------------
Without an Anthropic subscription/API key configured, every real call in
this codebase (`client.messages.create(...)`) would fail. Demo Mode gives
every one of those call sites a same-shaped, realistic-looking substitute
so the rest of the pipeline -- parsing, the review/edit UI, the upload
payload builders -- runs completely unchanged whether the content came
from Claude or from here.

Design choices worth knowing about:

- **Deterministic.** Output is seeded from the technology name / page
  title / breadcrumb, so re-generating the same page in a demo produces
  the same content (repeatable walkthroughs, stable screenshots) while
  different inputs still look different from each other.
- **Smaller than the real fan-out.** The real prompt asks for 8-15
  pillars x 4-10 modules x 5-12 chapters x 5-20 pages, which can be
  thousands of pages -- far more than a demo needs to click through.
  The demo skeleton uses a smaller, fixed range (6-8 / 2-3 / 2-3 / 3-4)
  so a full demo run stays fast and easy to present, while still
  exercising the exact same Pillar > Module > Chapter > Page shape.
- **Same output *shape* as the real call, always.** Each demo_* function
  below returns exactly the "generated" dict/list that the corresponding
  real `_ask_json(...)` call would have returned (pre-wrapping), so all
  the existing post-processing/wrapping code in ai_content.py and
  asset_generation.py is reused unmodified for both real and demo output.
"""
from __future__ import annotations

import hashlib
import random

# ---------------------------------------------------------------------
# Mode resolution
# ---------------------------------------------------------------------

# Substrings that mark an API key as an unfilled placeholder rather than
# a real credential (matches the placeholder shipped in .env.example).
_PLACEHOLDER_MARKERS = ("xxxxxxxx", "your-", "changeme", "sk-ant-xxx", "replace-me")


def is_key_configured(api_key: str | None) -> bool:
    """True only if api_key looks like a real, usable Anthropic key."""
    if not api_key:
        return False
    key = api_key.strip()
    if not key:
        return False
    lowered = key.lower()
    return not any(marker in lowered for marker in _PLACEHOLDER_MARKERS)


def resolve_demo_mode(api_key: str | None, mode: str | None = "auto") -> bool:
    """
    Decide whether Demo Mode should be active.

    mode:
      "auto" (default)  -> demo iff no usable Anthropic key is configured.
                            This is what makes the switch back to real AI
                            generation "seamless": as soon as a real
                            ANTHROPIC_API_KEY is set, every call site below
                            starts hitting the real API again with zero
                            code changes.
      "on"/"true"/"1"/"force"/"demo"
                         -> always demo, even with a real key configured
                            (useful for cost-free walkthroughs/demos on an
                            environment that *does* have credentials).
      "off"/"false"/"0"/"real"/"production"
                         -> never demo. A missing key still surfaces the
                            original "ANTHROPIC_API_KEY is required" error
                            from the real code path, unchanged.
    """
    normalized = (mode or "auto").strip().lower()
    if normalized in ("on", "true", "1", "force", "demo"):
        return True
    if normalized in ("off", "false", "0", "real", "production"):
        return False
    return not is_key_configured(api_key)


def _rng(*seed_parts: str) -> random.Random:
    seed = hashlib.sha256("||".join(str(p) for p in seed_parts).encode("utf-8")).hexdigest()
    return random.Random(int(seed[:16], 16))


def _parse_breadcrumb(breadcrumb: str) -> dict:
    """Breadcrumb lines look like 'Technology: X\\nPillar: ...\\nModule: ...
    \\nChapter: ...\\nPage: ...' (see ai_content._breadcrumb_context and
    assets._hierarchy_nodes) -- not every level is always present."""
    fields = {"technology": "", "pillar": "", "module": "", "chapter": "", "page": ""}
    for line in (breadcrumb or "").splitlines():
        if ":" not in line:
            continue
        key, _, value = line.partition(":")
        key = key.strip().lower()
        if key in fields:
            fields[key] = value.strip()
    return fields


# ---------------------------------------------------------------------
# 1. Curriculum skeleton (used by both the Architect tool and the AI
#    Pipeline -- same Pillar/Module/Chapter/Page markdown shape).
# ---------------------------------------------------------------------

_PILLAR_THEMES = [
    "Foundations", "Core Concepts", "Architecture & Internals",
    "Tooling & Developer Experience", "Concurrency & Performance",
    "Security & Hardening", "Testing & Quality Assurance",
    "Deployment & Operations", "Ecosystem & Integrations",
    "Enterprise Usage & Scale", "Advanced Patterns",
    "Interview & Career Readiness",
]

_MODULE_THEMES = [
    "Getting Started", "Key Building Blocks", "Under the Hood",
    "Common Workflows", "Best Practices", "Troubleshooting",
    "Real-World Scenarios", "Advanced Techniques",
]

_CHAPTER_THEMES = [
    "Overview", "Core Mechanics", "Practical Examples", "Edge Cases",
    "Comparisons & Alternatives", "Hands-On Exercise",
]

_PAGE_VERBS = [
    "Understanding", "Introduction to", "Deep Dive into", "Working with",
    "Configuring", "Debugging", "Optimizing", "Testing", "Securing",
    "Comparing Approaches to", "Common Pitfalls in", "Best Practices for",
]


def generate_demo_skeleton_markdown(technology_name: str, notes: str | None = None) -> str:
    """Same '# Pillar / ## Module / ### Chapter / Page' markdown shape the
    real Claude call produces, so ai_skeleton.parse_markdown_to_pillars()
    (and llm_service's callers, which just store the raw markdown) parse
    demo output identically to real output."""
    tech = (technology_name or "This Technology").strip()
    rng = _rng(tech, notes or "")

    n_pillars = rng.randint(6, 8)
    lines: list[str] = []
    for pi in range(1, n_pillars + 1):
        pillar_theme = _PILLAR_THEMES[(pi - 1) % len(_PILLAR_THEMES)]
        lines.append(f"# Pillar {pi} - {tech} {pillar_theme}")

        n_modules = rng.randint(2, 3)
        for mi in range(1, n_modules + 1):
            module_theme = _MODULE_THEMES[rng.randrange(len(_MODULE_THEMES))]
            lines.append(f"## Module {mi} - {module_theme}")

            n_chapters = rng.randint(2, 3)
            for ci in range(1, n_chapters + 1):
                chapter_theme = _CHAPTER_THEMES[rng.randrange(len(_CHAPTER_THEMES))]
                lines.append(f"### Chapter {ci} - {pillar_theme}: {chapter_theme}")

                n_pages = rng.randint(3, 4)
                for gi in range(1, n_pages + 1):
                    verb = _PAGE_VERBS[rng.randrange(len(_PAGE_VERBS))]
                    lines.append(f"Page {gi} - {verb} {tech} {chapter_theme}")

    return "\n".join(lines)


# ---------------------------------------------------------------------
# 2. Page content (ai_content.ContentGenerator / uploader.py payload)
# ---------------------------------------------------------------------

_CODE_LANGUAGE_HINTS = {
    "python": "python", "javascript": "javascript", "typescript": "typescript",
    "java": "java", "kubernetes": "yaml", "docker": "dockerfile", "sql": "sql",
    "postgres": "sql", "postgresql": "sql", "go": "go", "golang": "go",
    "rust": "rust", "react": "jsx", "c++": "cpp", "c#": "csharp",
}


def _guess_language(technology: str) -> str:
    lowered = (technology or "").lower()
    for needle, lang in _CODE_LANGUAGE_HINTS.items():
        if needle in lowered:
            return lang
    return "text"


def generate_demo_page_content(title: str, breadcrumb: str) -> dict:
    """Matches the raw shape ai_content.ContentGenerator._call() expects
    back from the model: {intro_html, explanation_html, code, key_points,
    quick_recap_html}."""
    fields = _parse_breadcrumb(breadcrumb)
    technology = fields["technology"] or "the technology"
    chapter = fields["chapter"] or fields["module"] or fields["pillar"] or "this topic"
    rng = _rng(title, breadcrumb)

    intro_html = (
        f"<p>This is sample (demo) content standing in for AI-generated "
        f"material. In production, Claude writes this page from scratch; "
        f"here it's a placeholder so you can preview the full page layout "
        f"for <strong>{title}</strong>, part of {chapter} in the {technology} "
        f"curriculum.</p>"
    )

    explanation_html = (
        f"<div>"
        f"<p>{title} covers a specific, well-scoped concept within {technology}, "
        f"building on what was introduced earlier in this chapter and preparing "
        f"the learner for what comes next.</p>"
        f"<p>In the real, AI-generated version of this page, this section would "
        f"contain a thorough, original explanation: how {title.lower()} works, "
        f"why it matters in real {technology} projects, and how it fits into "
        f"the broader system. It would be written specifically for this page's "
        f"place in the curriculum, not reused from any other page.</p>"
        f"</div>"
    )

    key_points = [
        f"{title} is a core idea within {chapter}.",
        f"Understanding it prepares you for later, more advanced {technology} topics.",
        "This is placeholder demo content -- real runs replace this with AI-generated detail.",
    ]

    quick_recap_html = (
        f"<div><strong>Quick Recap</strong><p>{title}: a foundational building "
        f"block for working with {technology}. (Demo content.)</p></div>"
    )

    code = None
    if rng.random() < 0.6:  # most pages get a sample snippet, same as real generation
        language = _guess_language(technology)
        code = {
            "language": language,
            "value": (
                f"// Demo placeholder snippet for: {title}\n"
                f"// Real runs generate an original, runnable example here.\n"
                f"function demoExample() {{\n"
                f'  console.log("{title} - sample output");\n'
                f"}}"
                if language in ("javascript", "typescript", "jsx")
                else (
                    f"# Demo placeholder snippet for: {title}\n"
                    f"# Real runs generate an original, runnable example here.\n"
                    f"def demo_example():\n"
                    f'    print("{title} - sample output")'
                )
            ),
        }

    return {
        "intro_html": intro_html,
        "explanation_html": explanation_html,
        "code": code,
        "key_points": key_points,
        "quick_recap_html": quick_recap_html,
    }


# ---------------------------------------------------------------------
# 3. FAQ
# ---------------------------------------------------------------------

def generate_demo_faq(title: str, breadcrumb: str) -> dict:
    """Matches asset_generation.generate_faq()'s raw shape: {faq_html}."""
    fields = _parse_breadcrumb(breadcrumb)
    technology = fields["technology"] or "this technology"

    qas = [
        ("Beginner", f"What is {title}?",
         f"{title} is a concept within {technology} covered at this point in the "
         f"curriculum. (Demo answer -- real runs generate a full explanation.)"),
        ("Intermediate", f"How does {title} fit into a typical {technology} project?",
         "It builds on earlier chapters and is used alongside related tooling "
         "in day-to-day development. (Demo answer.)"),
        ("Advanced", f"What are common edge cases with {title}?",
         "Real generated FAQs cover subtle failure modes, version differences, "
         "and non-obvious interactions here. (Demo answer.)"),
        ("Real-World", "How would this show up in a production system?",
         "Demo placeholder -- real runs describe a concrete production scenario."),
        ("Interview", f"How would you explain {title} in an interview?",
         "Demo placeholder -- real runs provide a concise, interview-ready answer."),
        ("Misconceptions", "What do people usually get wrong here?",
         "Demo placeholder -- real runs list genuine common misconceptions."),
    ]

    sections_html = []
    for group, q, a in qas:
        sections_html.append(
            f"<h2>{group}</h2><details><summary>{q}</summary><p>{a}</p></details>"
        )

    return {"faq_html": "".join(sections_html)}


# ---------------------------------------------------------------------
# 4/5. Example / Practice programs
# ---------------------------------------------------------------------

def generate_demo_program(title: str, breadcrumb: str, program_type: str) -> dict:
    """Matches asset_generation._generate_program()'s raw shape:
    {title, description, language, starterCode, solutionCode}."""
    fields = _parse_breadcrumb(breadcrumb)
    technology = fields["technology"] or "the technology"
    language = _guess_language(technology)
    label = "Worked Example" if program_type == "EXAMPLE" else "Practice Exercise"

    description = (
        f"<p>({label} -- demo content.) This program illustrates {title} in the "
        f"context of {technology}.</p>"
        + (
            "<p>Real runs include a full problem statement and an explanation "
            "of the approach and expected output.</p>"
            if program_type == "EXAMPLE"
            else
            "<p>Real runs include input/output examples, constraints, and a "
            "hint (without giving away the full solution).</p>"
        )
    )

    if language in ("javascript", "typescript", "jsx"):
        starter = f"// TODO: implement {title}\nfunction solve() {{\n  // your code here\n}}"
        solution = f"// Demo solution for: {title}\nfunction solve() {{\n  return true; // placeholder\n}}"
    else:
        starter = f"# TODO: implement {title}\ndef solve():\n    pass  # your code here"
        solution = f"# Demo solution for: {title}\ndef solve():\n    return True  # placeholder"

    return {
        "title": f"{title} ({label})",
        "description": description,
        "language": language,
        "starterCode": starter,
        "solutionCode": solution,
    }


# ---------------------------------------------------------------------
# 6/7/8. Chapter / Module / Pillar overview
# ---------------------------------------------------------------------

def generate_demo_overview(level_name: str, title: str, breadcrumb: str) -> dict:
    """Matches asset_generation._generate_overview()'s raw shape:
    {summary, html, highlights}."""
    fields = _parse_breadcrumb(breadcrumb)
    technology = fields["technology"] or "this technology"

    summary = f"A demo overview of the {title} {level_name} within the {technology} curriculum."
    html = (
        f"<div>"
        f"<p>This is placeholder demo content for the {title} {level_name} overview. "
        f"A real, AI-generated overview would describe objectives, prerequisites, "
        f"what's covered, and expected outcomes specific to {technology}.</p>"
        f"</div>"
    )
    highlights = [
        f"Covers key {level_name}-level concepts",
        "Builds on earlier material",
        "Demo content -- replace with a real run",
    ]
    return {"summary": summary, "html": html, "highlights": highlights}


# ---------------------------------------------------------------------
# 9. Flashcards
# ---------------------------------------------------------------------

def generate_demo_flashcards(title: str, breadcrumb: str, count: int = 12) -> dict:
    """Matches asset_generation.generate_flashcards()'s raw shape:
    {cards: [{front, back}, ...]}."""
    fields = _parse_breadcrumb(breadcrumb)
    technology = fields["technology"] or "this technology"

    cards = []
    for i in range(1, count + 1):
        cards.append({
            "front": f"Demo flashcard #{i} for {title}: key term or question?",
            "back": f"Demo answer -- a real run generates genuine {technology}-specific content here.",
        })
    return {"cards": cards}


# ---------------------------------------------------------------------
# 10. Module quiz
# ---------------------------------------------------------------------

_QUESTION_DIFFICULTIES = ["BEGINNER", "BEGINNER", "INTERMEDIATE", "INTERMEDIATE", "ADVANCED"]


def generate_demo_quiz(title: str, breadcrumb: str, num_questions: int = 10) -> dict:
    """Matches asset_generation.generate_module_quiz()'s raw shape:
    {questions: [...]}."""
    fields = _parse_breadcrumb(breadcrumb)
    technology = fields["technology"] or "this technology"
    rng = _rng(title, breadcrumb)

    questions = []
    for i in range(1, num_questions + 1):
        difficulty = _QUESTION_DIFFICULTIES[rng.randrange(len(_QUESTION_DIFFICULTIES))]
        questions.append({
            "prompt": f"(Demo Q{i}) Which statement about {title} in {technology} is correct?",
            "questionType": "SINGLE_CHOICE",
            "codeSnippet": "",
            "codeLanguage": "",
            "options": [
                {"label": "Demo correct answer -- a real run writes genuine options.", "correct": True},
                {"label": "Demo distractor A", "correct": False},
                {"label": "Demo distractor B", "correct": False},
                {"label": "Demo distractor C", "correct": False},
            ],
            "explanation": "Demo explanation -- real runs explain why the correct answer is correct.",
            "difficulty": difficulty,
        })
    return {"questions": questions}
