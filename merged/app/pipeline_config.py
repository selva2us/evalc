"""
Small helpers for driving elluval_pipeline.Pipeline from web requests.

Each "run" (one technology's skeleton -> review -> submit -> content ->
upload flow) gets its own work_dir under instance/pipeline_runs/<run_id>.
elluval_pipeline.Pipeline itself is stateless per-instantiation and reads
its checkpoint from a state.json file inside that work_dir (see
elluval_pipeline/state.py), so re-creating a Pipeline(work_dir=...) on
every HTTP request and calling the next stage is exactly how it's meant
to be driven -- no in-memory session state required beyond the run_id.

Credentials (BASE_URL, API_TOKEN, ANTHROPIC_API_KEY, etc.) are read by
elluval_pipeline.config.load_config() from the environment / .env, same
as when the package is run as a CLI. See elluval_pipeline/config.py for
the full list of required/optional variables.
"""
from __future__ import annotations

import uuid
from pathlib import Path

PIPELINE_ROOT = Path(__file__).resolve().parent.parent / "instance" / "pipeline_runs"


def new_run_id() -> str:
    return uuid.uuid4().hex[:12]


def work_dir_for(run_id: str) -> Path:
    path = PIPELINE_ROOT / run_id
    path.mkdir(parents=True, exist_ok=True)
    return path
