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

## API usage (Architect tool)

```bash
curl -X POST http://localhost:5000/api/generate \
  -H "Content-Type: application/json" \
  -d '{"technology_name": "Kubernetes"}'
```
