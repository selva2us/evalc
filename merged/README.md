# Curriculum Suite

One Flask app combining two previously separate tools:

1. **Curriculum Architect** (was `curriculum-generator/`) — a quick
   markdown skeleton generator: type a technology, get a
   Pillar → Module → Chapter → Page outline, saved to SQLite.
2. **AI Pipeline** (was `elluval_pipeline_ai/`, a CLI tool) — the full
   generate → review → submit-to-backend → generate-page-content → upload
   flow, now driven from the browser instead of the terminal.

They share one Flask app, one `requirements.txt`, and the same
`ANTHROPIC_API_KEY`. Nothing else about either tool's logic changed —
the merge is purely at the web-app layer.

## Project layout

```
curriculum-suite/
├── app/
│   ├── __init__.py            # app factory, registers all 3 blueprints
│   ├── config.py               # Flask/db/Anthropic config (dev/prod/testing)
│   ├── extensions.py           # shared SQLAlchemy instance
│   ├── models.py               # Curriculum model (Architect tool)
│   ├── pipeline_config.py      # NEW: per-run work_dir helper for the Pipeline
│   ├── services/
│   │   └── llm_service.py      # Architect tool's prompt + Anthropic call
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
| `ANTHROPIC_API_KEY` | both tools | yes |
| `ANTHROPIC_MODEL` | Architect tool | no (default `claude-sonnet-5`) |
| `DATABASE_URL` | Architect tool | no (default local SQLite) |
| `BASE_URL` | AI Pipeline (your curriculum backend) | yes, to use `/pipeline` |
| `API_TOKEN` | AI Pipeline (bearer token) | yes, to use `/pipeline` |
| `API_COOKIE_FILE` | AI Pipeline (optional session cookie) | no |
| `SKELETON_MODEL`, `CONTENT_MODEL` | AI Pipeline | no |

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

## API usage (Architect tool)

```bash
curl -X POST http://localhost:5000/api/generate \
  -H "Content-Type: application/json" \
  -d '{"technology_name": "Kubernetes"}'
```
