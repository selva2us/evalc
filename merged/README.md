# Curriculum Suite

One Flask app combining two previously separate tools:

1. **Curriculum Architect** (was `curriculum-generator/`) — a quick
   markdown skeleton generator: type a technology, get a
   Pillar → Module → Chapter → Page outline, saved to SQLite.
2. **AI Pipeline** (was `elluval_pipeline_ai/`, a CLI tool) — the full
   generate → review → submit-to-backend → generate-page-content → upload
   flow, now driven from the browser instead of the terminal.

They share one Flask app, one `requirements.txt`, and one active AI
provider selection (`LLM_PROVIDER`). Nothing else about either tool's logic
changed — the merge is purely at the web-app layer.

> **Multi-model support:** this app can call **Anthropic (Claude)**,
> **OpenAI (GPT)**, or **Google Gemini** — pick which one is active via the
> `LLM_PROVIDER` env var or the admin Settings page (`/admin/settings`),
> no restart or code changes needed. See
> `elluval_pipeline/llm_providers.py` for the provider abstraction.

## Project layout

```
curriculum-suite/
├── app/
│   ├── __init__.py            # app factory, registers all 3 blueprints
│   ├── config.py               # Flask/db/AI-provider config (dev/prod/testing)
│   ├── extensions.py           # shared SQLAlchemy instance
│   ├── models.py               # Curriculum model (Architect tool)
│   ├── pipeline_config.py      # NEW: per-run work_dir helper for the Pipeline
│   ├── services/
│   │   └── llm_service.py      # Architect tool's prompt + provider call
│   ├── routes/
│   │   ├── main.py             # Architect: index, generate, result, history
│   │   ├── api.py               # Architect: JSON API
│   │   └── pipeline.py         # NEW: web wizard around elluval_pipeline
│   ├── templates/
│   │   ├── ...                 # Architect pages
│   │   └── pipeline/           # NEW: pipeline wizard pages
│   └── static/style.css
├── elluval_pipeline/            # the original pipeline package, unchanged
│   ├── pipeline.py             # Pipeline class (both PDF and AI flows)
│   ├── ai_skeleton.py          # skeleton generation + parsing
│   ├── ai_content.py           # per-page content generation
│   ├── uploader.py, config.py, state.py, ...
├── instance/
│   ├── curriculum.db            # Architect tool's SQLite db (gitignored)
│   └── pipeline_runs/<run_id>/  # NEW: per-run work_dir (gitignored)
├── run.py
├── requirements.txt
└── .env.example
```

## How the merge works

- **`elluval_pipeline/`** was copied in as-is and is imported as a normal
  Python package (`from elluval_pipeline.pipeline import Pipeline`). Its
  core logic (`Pipeline`, `ai_skeleton`, `ai_content`, `uploader`,
  `config.load_config`, `state.StateStore`) was **not modified**.
- The pipeline's CLI used `input()` at three points: entering a Subject ID,
  entering a Document ID, and confirming "ok" to a skeleton shown in a
  browser tab. The web wizard (`app/routes/pipeline.py`) replaces all
  three with actual HTML forms instead of terminal prompts:
  - Reviewing the skeleton happens on a page (`/pipeline/review/<run_id>`)
    that renders `skeleton.md` directly, instead of opening a browser tab
    from the CLI process.
  - Document ID / Subject ID are collected via a form on that same page
    instead of `input()`. Setting `cfg.subject_id` before calling
    `generate_content_ai()`/`upload()` is what makes
    `Pipeline.prompt_for_subject_id()` a no-op (it only prompts when
    `cfg.subject_id` is falsy) — no changes to `pipeline.py` needed.
- Each run gets its own folder under `instance/pipeline_runs/<run_id>/`.
  The pipeline's own `StateStore` (a JSON checkpoint file it already
  writes there) is what makes a **stateless HTTP request/redirect cycle**
  work for a multi-stage process: every route just does
  `Pipeline(work_dir=...)` and calls the next stage — no in-memory session
  state is needed beyond the `run_id` in the URL.
- Both tools already used the same environment variable for the model key
  (`ANTHROPIC_API_KEY`), so no renaming was needed there.

## Setup

```bash
python -m venv .venv
source .venv/bin/activate        # Windows: .venv\Scripts\activate
pip install -r requirements.txt

cp .env.example .env
# then edit .env — see below for what's required by each tool
```

`.env` variables:

| Variable | Used by | Required |
|---|---|---|
| `SECRET_KEY` | Flask sessions/flash | recommended |
| `APP_USERNAME`, `APP_PASSWORD` | Login (see below) | no -- app is open (as before) until both are set |
| `ANTHROPIC_API_KEY` | both tools | no -- falls back to Demo Mode (see below) |
| `ANTHROPIC_MODEL` | Architect tool | no (default `claude-sonnet-5`) |
| `DATABASE_URL` | Architect tool | no (default local SQLite) |
| `BASE_URL` | AI Pipeline (your curriculum backend) | yes, to use `/pipeline` |
| `API_TOKEN` | AI Pipeline (bearer token) | yes, to use `/pipeline` |
| `API_COOKIE_FILE` | AI Pipeline (optional session cookie) | no |
| `SKELETON_MODEL`, `CONTENT_MODEL` | AI Pipeline | no |

`ANTHROPIC_API_KEY`, `BASE_URL`, `API_TOKEN`, and the model settings can all
also be set later through `/admin/settings` after logging in, instead of
editing `.env` by hand -- see "Admin: Settings, Cookies, and Prompts" below.

The Architect tool (`/`, `/history`, `/api/*`) works with just
`ANTHROPIC_API_KEY`. The AI Pipeline (`/pipeline/*`) also needs
`BASE_URL`/`API_TOKEN` for your actual curriculum backend, since it
submits the reviewed skeleton and uploads generated content there.

## Run (development)

```bash
python run.py
```

Visit `http://localhost:5000`:

- `/` — Curriculum Architect (single-shot skeleton generation)
- `/pipeline/` — AI Pipeline (skeleton → review → submit → content → upload)

## Run (production)

```bash
gunicorn -w 4 -b 0.0.0.0:8000 run:app
```

Note: `/pipeline/submit/<run_id>` generates content for every page in the
skeleton synchronously (one Anthropic call per page) before responding —
fine for small/medium skeletons, but for large ones consider moving
`generate_content_ai()`/`upload()` onto a background worker (Celery/RQ)
so the request doesn't hold a worker process for minutes. `Pipeline` and
its `StateStore` are already resumable/idempotent per page, so this is a
drop-in change, not a redesign.

## AI Pipeline: Review Mode (optional)

By default, `/pipeline/submit/<run_id>` behaves exactly as before: generate
and upload every page's content automatically, then show a result summary.

A new **optional** "Manual Review" choice on the review page switches to a
page-by-page workflow instead, without changing anything about the default
path:

1. Submits the skeleton, same as always.
2. For each page (in document order): generates its content (if not already
   generated), shows a rendered preview plus an editable JSON textarea for
   the exact `{title, sections}` payload, and lets you Approve, Regenerate,
   or Skip.
3. Approving a page uploads **only that page** via the same
   `POST /api/pages/<pageId>/content` call the automatic flow already uses
   — the payload contract is untouched — then automatically advances to the
   next page.
4. Once every page is approved or skipped, you land on the same result
   summary page the automatic flow uses.

This is implemented entirely in `app/routes/pipeline.py` alongside the
existing routes; the automatic flow's code path is untouched (verified: an
old client that never sends a `mode` field gets identical behavior to
before). Per-run metadata (document/subject id) needed to drive the
page-by-page routes statelessly is cached in
`instance/pipeline_runs/<run_id>/run_meta.json`; per-page approve/skip
progress is tracked in a `review_upload` stage inside that run's existing
`state.json`, kept separate from the automatic flow's own `content`/`upload`
stages so the two workflows can't interfere with each other.

## Asset Studio: FAQs, programs, overviews, flashcards, quizzes (optional)

A third, fully optional layer on top of the two above: `/pipeline/assets/<run_id>`,
linked from the result page after a successful run. Generates and submits
eight additional educational asset types, each through the same
generate → review/edit → approve → submit → skip workflow as page-content
Review Mode:

| Asset | Applicable to | Endpoint |
|---|---|---|
| FAQ | page / chapter / module / pillar | `POST /api/pages/<id>/content` (same shape as page content, `title: "FAQs"`) |
| Example Program | page / chapter | `POST /api/compiler/practice/chapter/<id>` (`programType: "EXAMPLE"`) |
| Practice Program | page / chapter | `POST /api/compiler/practice/chapter/<id>` (`programType: "PRACTICE"`) |
| Chapter Overview | chapter | `PUT /api/curriculum/chapters/<id>/overview` |
| Module Overview | module | `PUT /api/curriculum/modules/<id>/overview` |
| Pillar Overview | pillar | `PUT /api/curriculum/pillars/<id>/overview` |
| Flashcards | module | `PUT /api/curriculum/modules/<id>/flashcards` (payload is a JSON **list**) |
| Module Quiz | module | `PUT /api/curriculum/modules/<id>/quiz?subjectId=<id>` |

Notes:

- **Fully additive.** Lives in its own module (`elluval_pipeline/asset_generation.py`)
  and its own blueprint (`app/routes/assets.py`, mounted at `/pipeline/assets`),
  with its own storage (`instance/pipeline_runs/<run_id>/assets/`). Nothing
  in the skeleton/page-content pipeline calls into it, and nothing about
  the existing routes' behavior changed to add it (verified with
  regression tests: legacy auto-mode and page-content Review Mode behave
  identically with or without this feature present).
- **Target ID.** Some asset types' real endpoint is scoped one level
  coarser than where you might generate them (e.g. a page-level "Example
  Program" still POSTs to its parent chapter's endpoint, since that's the
  only endpoint given for programs). The review page auto-suggests a
  target ID by matching titles against the live subject tree, but always
  shows it as an editable field — confirm or correct it before approving.
- **Program `testCases`.** The reference payload only showed an empty
  list for `testCases`, so generated example/practice programs leave it
  empty for you to fill in during review rather than guessing at a shape
  that wasn't specified.
- Skipping an asset leaves it as `pending`/`skipped` in
  `assets/<asset_id>.json` so you can come back to it later from the hub.

## Demo Mode (no Anthropic subscription required)

Every place in this app that would otherwise call the Anthropic API --
`app/services/llm_service.py` (Architect tool), `elluval_pipeline/ai_skeleton.py`
and `ai_content.py` (AI Pipeline skeleton + page content), and
`elluval_pipeline/asset_generation.py` (FAQs, example/practice programs,
overviews, flashcards, quizzes) -- automatically falls back to realistic,
deterministic sample content whenever no usable `ANTHROPIC_API_KEY` is
configured. This is controlled by the shared `DEMO_MODE` variable:

| `DEMO_MODE` | Behavior |
|---|---|
| `auto` (default) | Demo content whenever `ANTHROPIC_API_KEY` is missing/blank/a placeholder; real Anthropic calls the moment a real key is set. No code changes needed either direction. |
| `on` | Always demo content, even if a real key is configured (handy for cost-free walkthroughs/demos). |
| `off` | Never demo content; a missing key raises the original `ANTHROPIC_API_KEY is required` error, unchanged from before Demo Mode existed. |

What this means in practice:

- **Right now, with no `ANTHROPIC_API_KEY`**, you can already run through the
  entire product: draft a curriculum on `/`, run the full AI Pipeline wizard
  on `/pipeline/` (skeleton -> review -> submit -> generate content -> Review
  Mode page-by-page approval), and generate every Asset Studio type (FAQs,
  example/practice programs, chapter/module/pillar overviews, flashcards,
  module quizzes) on `/pipeline/assets/<run_id>` -- all with realistic-looking
  placeholder content instead of errors.
- **A yellow/teal "Demo Mode" banner** appears at the top of every page while
  it's active, so it's never ambiguous whether you're looking at real or
  sample content. Generated curricula also record `demo-mode (sample
  content)` as their "model" in the Architect tool's history/result pages.
- **Demo content is deterministic**, seeded from the technology name / page
  title, so re-running the same demo produces the same output -- useful for
  repeatable stakeholder walkthroughs and stable screenshots.
- **The demo curriculum skeleton is intentionally smaller** than the real
  8-15 pillar / 4-10 module / 5-12 chapter / 5-20 page fan-out (it uses a
  fixed 6-8 / 2-3 / 2-3 / 3-4 range instead), so a full demo run stays fast
  to click through while still exercising the exact same Pillar > Module >
  Chapter > Page hierarchy end-to-end.
- **Once you add a real `ANTHROPIC_API_KEY`** (and remove/unset `DEMO_MODE`,
  or leave it on `auto`), the app switches to real, technology-specific AI
  generation automatically -- nothing else to configure, and the existing
  Anthropic integration itself was not modified.

All of this lives in one new module, `elluval_pipeline/demo_content.py`,
plus a small "if demo mode: use demo_content instead" branch inside each of
the four generation call sites above -- nothing about the real
generation code paths, the review/edit UI, or the upload payload shapes
was changed.

Note: Demo Mode only covers **content generation** (the Anthropic calls).
Submitting/uploading to your curriculum backend still uses the real
`BASE_URL`/`API_TOKEN` you configure for `/pipeline` and `/pipeline/assets`
-- that integration is unrelated to Anthropic and wasn't touched.

## Login

The whole app can sit behind a login screen (`app/auth.py`), gated by a
single app-wide `before_request` hook -- no existing route, view function,
or template had to change to get this. Credentials come from environment
variables, never hardcoded:

```env
APP_USERNAME=admin
APP_PASSWORD=supersecretpassword
SESSION_LIFETIME_MINUTES=480   # optional, defaults to 8 hours
```

**Opt-in, by design:** if `APP_USERNAME`/`APP_PASSWORD` aren't both set, the
login gate is skipped entirely and the app behaves exactly as it did before
this feature existed -- open, no login required. This is deliberate:
upgrading shouldn't silently lock an existing deployment out of its own
app. Configuring both env vars *is* what turns login on. Once both are set:

- Every route requires a logged-in session; unauthenticated `GET` requests
  redirect to `/login?next=<original path>`.
- A successful login sets a permanent, signed Flask session cookie that
  expires after `SESSION_LIFETIME_MINUTES` of inactivity (sliding window --
  each request resets the clock) or on explicit logout.
- Credentials are compared with `hmac.compare_digest` (constant-time, avoids
  leaking match-length via response timing).
- "Log out" appears in the top nav and posts to `/logout`, which clears the
  session outright.

## Admin: Settings, Cookies, and Prompts

Once logged in, three admin pages appear in the top nav:

**`/admin/settings`** -- view/update the runtime configuration values below.
Values are masked (`sk-a••••••••7890`) once configured; secret fields render
as password inputs. Saving a value writes it to `os.environ`,
`current_app.config`, *and* the project's `.env` file, all in the same
request -- so a change is live immediately (no restart) and still there
after one. This is what makes "add a real `ANTHROPIC_API_KEY` and it just
works" possible without touching a terminal at all:

| Setting | Used by |
|---|---|
| `ANTHROPIC_API_KEY` | Both tools -- the shared Anthropic credential |
| `ANTHROPIC_MODEL` | Architect tool |
| `SKELETON_MODEL` / `CONTENT_MODEL` | AI Pipeline / Asset Studio |
| `DEMO_MODE` | Both -- see Demo Mode above |
| `BASE_URL` / `API_TOKEN` | Curriculum backend (AI Pipeline/Asset Studio submission) |

Adding a future AI provider key or config value is a one-line addition to
`SETTINGS_FIELDS` in `app/services/settings_service.py` -- no route or
template changes needed.

**`/admin/cookies`** -- manage `CF_AppSession`, `CF_Authorization`, and
`cf_clearance`, for curriculum backends that sit behind Cloudflare Access /
Bot Management. Add, update, delete, or clear all; the page only ever shows
*whether* a cookie is configured and when it was last updated, never the
value itself. Saving regenerates `cookies.txt` (one `NAME=VALUE` per line),
which `elluval_pipeline/config.py`'s existing `API_COOKIE_FILE` mechanism
already reads into the `Cookie` header sent on every curriculum-backend
request -- that mechanism predates this feature and was only extended
(backward-compatibly) to also accept this multi-line format, in addition to
the single raw cookie string it already supported.

**`/admin/prompts`** -- view and edit the centralized system prompt files
directly (see below), with edits taking effect on the very next generation
call (the in-memory prompt cache is cleared on save).

## Centralized prompt management

Every system prompt used anywhere in the suite -- the Architect tool, the
AI Pipeline (skeleton + page content), Asset Studio (FAQs, example/practice
programs, overviews, flashcards, quizzes), and even the legacy PDF-based
flow -- lives as a plain-text file under `prompts/`, not hardcoded inside
services, routes, or pipeline files:

```text
prompts/
├── curriculum_system_prompt.txt   Architect tool skeleton prompt
├── skeleton_prompt.txt            AI Pipeline skeleton prompt
├── content_generation_prompt.txt  AI Pipeline page content prompt
├── faq_prompt.txt
├── program_prompt.txt             shared by example + practice programs
├── overview_prompt.txt            shared by chapter/module/pillar overviews
├── flashcard_prompt.txt
├── quiz_prompt.txt
├── legacy_rewriter_prompt.txt         legacy PDF-flow content prompt
└── legacy_pillar_grouping_prompt.txt  legacy PDF-flow pillar grouping prompt
```

`elluval_pipeline/prompts.py` is the loader (`get_prompt(name, **kwargs)`),
used by every one of the files above instead of an inline string constant.
This was a pure relocation -- every prompt's exact wording was verified
byte-for-byte identical to what was previously hardcoded, so no generation
behavior changed for existing users. Prompts are cached in-process (they're
requested on every single generation call); the admin Prompts page clears
that cache on save so edits apply immediately.

Dynamic prompts use `$identifier` placeholders (Python's `string.Template`,
via `safe_substitute`) rather than `str.format()`, because several prompts
instruct the model to respond in a literal JSON `{...}` shape -- `.format()`
would choke on those braces or require an unreadable amount of escaping.
`$-`-style substitution ignores plain `{ }` entirely, which is also what
makes the prompt files safe for a non-developer to edit directly through
`/admin/prompts` without needing to understand Python string formatting.

## API usage (Architect tool)

```bash
curl -X POST http://localhost:5000/api/generate \
  -H "Content-Type: application/json" \
  -d '{"technology_name": "Kubernetes"}'
```
