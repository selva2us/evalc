"""
cli_ai.py
=========
Command-line entry point for the new PDF-free flow:

    python -m elluval_pipeline.cli_ai "Kubernetes"
    python -m elluval_pipeline.cli_ai "Kubernetes" --notes "focus on SRE audience" --document-id 7

Steps performed (see pipeline.Pipeline.run_ai for details):
  1. Generate the curriculum skeleton with Claude.
  2. Open skeleton.md in the browser for review; type 'ok' in the
     terminal to continue, anything else aborts (the file stays put).
  3. Submit to /api/documents/syllabus-import/<document-id>.
  4. Prompt for the Subject ID used to fetch/upload page content.
  5. Generate full content for every page with Claude.
  6. Upload each page's content to the curriculum API.
"""
from __future__ import annotations

import argparse

from .pipeline import Pipeline


def main():
    parser = argparse.ArgumentParser(description="Generate and submit a curriculum via Claude, no PDF required.")
    parser.add_argument("technology_name", help="e.g. 'Kubernetes', 'React', 'PostgreSQL'")
    parser.add_argument("--notes", default=None, help="Optional free-text context: audience, depth, focus areas")
    parser.add_argument("--document-id", default=None, help="Target id for /api/documents/syllabus-import/<id>")
    parser.add_argument("--work-dir", default=None, help="Override the working/output directory")
    args = parser.parse_args()

    pipeline = Pipeline(work_dir=args.work_dir)
    pipeline.run_ai(args.technology_name, notes=args.notes, document_id=args.document_id)


if __name__ == "__main__":
    main()
