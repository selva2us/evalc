# elluval_pipeline — AI-Driven Flow (No PDF)

This adds a second entry point to the existing pipeline: instead of parsing
a source PDF, you give it a **technology name only**, Claude drafts the full
Pillar → Module → Chapter → Page skeleton, you review it in one browser
screen, and on approval it's submitted straight to your platform.

## What's new

| File | Purpose |
|---|---|
| `ai_skeleton.py` | Generates the skeleton markdown via Claude, parses it into the same tree shape the rest of the pipeline already uses, writes `skeleton.md`/`skeleton.json`, opens the `.md` in the browser for review, and prompts for a terminal "ok" to proceed. |
| `ai_content.py` | Generates full page content (intro, explanation, code example, key points, quick recap) for every page in the skeleton — same output shape `uploader.py` already expects, so that stage is unchanged. |
| `api_client.py` | Added `import_syllabus()` — `POST /api/documents/syllabus-import/<document_id>`. |
| `config.py` | Added `DOCUMENT_ID`, `SKELETON_MODEL`, `CONTENT_MODEL` env vars. |
| `pipeline.py` | Added `Pipeline.run_ai(...)` orchestrating the new flow; the original `run()` (PDF-based) is untouched. |
| `cli_ai.py` | Command-line entry point for the new flow. |

Everything else (`uploader.py`, `state.py`, `logging_setup.py`, and the
original PDF-based `skeleton.py` / `extractor.py` / `rewriter.py` /
`pdf_builder.py` / `module_visual.py`) is unchanged — the old PDF flow still
works exactly as before via `Pipeline(pdf_path).run()`.

## ⚠️ One thing to confirm: the syllabus-import payload

You mentioned you'd attached the real request-body spec for
`/api/documents/syllabus-import/<id>`, but it didn't come through on this
pass. `CurriculumClient.import_syllabus()` currently sends:

```json
{
  "technologyName": "...",
  "markdown": "...",
  "tree": [ /* the same pillars/modules/chapters/pages array as skeleton.json */ ]
}
```

This is a reasonable guess, not a confirmed contract. Open
`api_client.py`, find `import_syllabus()`, and adjust the `payload` dict to
match the real field names — nothing else in the pipeline depends on this
shape, so it's a one-place fix.

## Flow, step by step

```bash
python -m elluval_pipeline.cli_ai "Kubernetes" --document-id 7
```

1. **Generate skeleton.** Claude drafts the outline using the same
   validated "curriculum architect" prompt (8–15 pillars, full topic
   coverage, beginner→expert progression). Written to
   `work/skeleton.md` and `work/skeleton.json`.
2. **Review.** `work/skeleton.md` opens in your default browser
   automatically (as a `file://` URL, full screen, one file). It is never
   deleted or overwritten silently — it's your permanent review copy for
   this run. Back in the terminal:
   ```
   Reviewed the skeleton? Type 'ok' to submit, anything else to abort:
   ```
   Typing anything other than `ok`/`y`/`yes` stops the pipeline here. The
   file stays put; re-run the command to regenerate, or just re-open the
   existing file to review again.
3. **Submit.** On approval, the skeleton (markdown + parsed tree) is
   POSTed to `/api/documents/syllabus-import/<document_id>`.
4. **Subject ID prompt.** Same pause point as the original pipeline — enter
   the Subject ID used to fetch/upload the curriculum tree (this may or may
   not be the same numeric id as `document_id`, depending on how your
   backend wires an import to a subject).
5. **Generate content.** Claude writes full content — intro, explanation,
   one worked example where relevant, key points, quick recap — for every
   single page in the skeleton, using each page's Pillar/Module/Chapter
   breadcrumb as context (no PDF text to reference; it's original content
   grounded in the model's own knowledge of the technology).
6. **Upload.** The existing `uploader.py` stage fetches the live curriculum
   tree, matches each generated page by title, uploads any images, and
   POSTs the content — unchanged from the original pipeline.

Every stage is resumable via the same `StateStore` used elsewhere: re-run
the same command on an interrupted run and it picks up where it left off
(content already generated for a page won't be regenerated, uploads already
done won't be redone).

## Environment variables (in addition to the existing ones)

```bash
DOCUMENT_ID=7                      # target id for syllabus-import; can also
                                    # be passed as --document-id
SKELETON_MODEL=claude-sonnet-4-6   # model used to draft the outline
CONTENT_MODEL=claude-sonnet-4-6    # model used to write page content
```

`ANTHROPIC_API_KEY` (already required by the existing `rewriter.py`) powers
both new stages.

## Using it from code instead of the CLI

```python
from elluval_pipeline.pipeline import Pipeline

pipeline = Pipeline(work_dir="./work")
pipeline.run_ai("Kubernetes", notes="focus on SRE audience", document_id="7")
```

Or drive each step yourself if you want a custom review UI later instead of
the browser-open + terminal-confirm approach:

```python
skel = pipeline.generate_skeleton_ai("Kubernetes")
# ... show pipeline.cfg.work_dir / "skeleton.md" however you like ...
if user_approved:
    pipeline.submit_syllabus(skel, document_id="7")
    pipeline.prompt_for_subject_id()
    pipeline.generate_content_ai(skel)
    pipeline.upload()
```
