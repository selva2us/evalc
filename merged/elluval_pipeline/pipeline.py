"""
pipeline.py
===========
Orchestrates the full workflow. Two entry points now exist:

  PDF-based (original): Pipeline(pdf_path).run()
    1. Parse the PDF into a skeleton/template structure.
    2. Pause and wait for the user to enter a Subject ID.
    3. Fetch curriculum data, rewrite content from the PDF, insert
       images/diagrams, and produce the final PDF automatically.

  AI-driven (new, no PDF): Pipeline(technology_name=...).run_ai(technology_name)
    1. Ask Claude to draft the full skeleton from just a technology name.
    2. Open skeleton.md in the browser for a one-screen review; the user
       types 'ok' in the terminal to proceed, or aborts.
    3. Submit the approved skeleton to /api/documents/syllabus-import/<id>.
    4. Prompt for the Subject ID used to fetch the curriculum tree.
    5. Ask Claude to write full content (explanation + example) for every
       page in the skeleton.
    6. Upload each page's content via the existing uploader.py stage.

Every stage is resumable via StateStore - re-running on the same work_dir
skips stages/items already marked done, so a large PDF or a large subject
can be restarted after an interruption without redoing work.
"""
from __future__ import annotations

from pathlib import Path

from . import ai_content, ai_skeleton, extractor, module_visual, pdf_builder, rewriter, skeleton, uploader
from .api_client import CurriculumClient
from .config import load_config
from .logging_setup import setup_logging
from .state import StateStore


class Pipeline:
    def __init__(self, pdf_path: str | None = None, work_dir: str | None = None):
        self.pdf_path = pdf_path
        self.cfg = load_config()
        if work_dir:
            self.cfg.work_dir = Path(work_dir).resolve()
            self.cfg.work_dir.mkdir(parents=True, exist_ok=True)
        self.logger = setup_logging(self.cfg.work_dir)
        self.state = StateStore(self.cfg.work_dir / "state.json")

    # -- Stage 1 ------------------------------------------------------
    def generate_skeleton(self) -> dict:
        if self.state.is_done("skeleton", "generate"):
            self.logger.info("Skeleton already generated, loading cached copy")
            import json
            return json.loads((self.cfg.work_dir / "skeleton.json").read_text())

        self.logger.info("=== Stage 1: Generating PDF skeleton ===")
        skel = skeleton.generate_skeleton(self.pdf_path, self.cfg.work_dir, self.cfg, self.logger)
        self.state.mark("skeleton", "generate", "done")
        return skel

    # -- Pause point ----------------------------------------------------
    def prompt_for_subject_id(self) -> str:
        if self.cfg.subject_id:
            self.logger.info("Using SUBJECT_ID from environment: %s", self.cfg.subject_id)
            return self.cfg.subject_id
        subject_id = input("\nSkeleton generated. Enter the Subject ID to continue: ").strip()
        while not subject_id:
            subject_id = input("Subject ID cannot be empty. Enter the Subject ID: ").strip()
        self.cfg.subject_id = subject_id
        return subject_id

    # -- Stage 2: extract -------------------------------------------------
    def extract(self, skel: dict):
        self.logger.info("=== Stage 2: Extracting content ===")
        return extractor.extract_all(skel, self.cfg.work_dir, self.logger)

    # -- Stage 3: rewrite -------------------------------------------------
    def rewrite(self):
        self.logger.info("=== Stage 3: Rewriting content for Indian students ===")
        return rewriter.rewrite_all(
            self.cfg.work_dir / "extracted",
            self.cfg.work_dir / "rewritten",
            self.cfg, self.logger, state=self.state,
        )

    # -- Stage 4: upload --------------------------------------------------
    def upload(self):
        self.logger.info("=== Stage 4: Fetching curriculum + uploading content ===")
        uploader.upload_all(self.cfg.work_dir / "rewritten", self.cfg, self.logger, state=self.state)

    def apply_module_visuals(self, image_folder: str = "module_diagrams_png"):
        self.logger.info("=== Stage 4b: Applying module overview visuals ===")
        module_visual.apply_module_visuals(self.cfg, self.logger, image_folder, state=self.state)

    # -- Stage 5: final PDF -------------------------------------------------
    def build_final_pdf(self, skel: dict) -> Path:
        self.logger.info("=== Stage 5: Building final PDF ===")
        out_path = self.cfg.work_dir / f"subject_{self.cfg.subject_id}_final.pdf"
        return pdf_builder.build_pdf(
            skel, self.cfg.work_dir / "rewritten", out_path, self.logger,
            image_root=self.cfg.work_dir,
        )

    # -- Full run (PDF-based) -----------------------------------------------
    def run(self, apply_module_diagrams: bool = False):
        skel = self.generate_skeleton()
        self.prompt_for_subject_id()

        self.extract(skel)
        self.rewrite()
        self.upload()
        if apply_module_diagrams:
            self.apply_module_visuals()
        final_pdf = self.build_final_pdf(skel)

        self.logger.info("Pipeline complete. Final PDF: %s", final_pdf)
        return final_pdf

    # =========================================================================
    # New AI-driven flow - no PDF. Technology name in, reviewed skeleton
    # submitted to /api/documents/syllabus-import/<document_id>, then every
    # page's content generated via Claude and uploaded via the existing
    # uploader.py stage.
    # =========================================================================

    def generate_skeleton_ai(self, technology_name: str, notes: str | None = None) -> dict:
        if self.state.is_done("ai_skeleton", "generate"):
            self.logger.info("AI skeleton already generated, loading cached copy")
            import json
            return json.loads((self.cfg.work_dir / "skeleton.json").read_text())

        self.logger.info("=== Stage 1 (AI): Generating curriculum skeleton for '%s' ===", technology_name)
        skel = ai_skeleton.generate_skeleton(technology_name, self.cfg.work_dir, self.cfg, self.logger, notes=notes)
        self.state.mark("ai_skeleton", "generate", "done")
        return skel

    def review_skeleton(self) -> bool:
        """Opens skeleton.md in the browser and waits for the user to type
        'ok' in the terminal. Returns False if the user aborts - the file
        is left in place either way so it can be reopened/edited/reviewed
        again on a later run."""
        md_path = self.cfg.work_dir / "skeleton.md"
        ai_skeleton.open_for_review(md_path, self.logger)
        approved = ai_skeleton.confirm_with_user()
        if not approved:
            self.logger.info("Skeleton not approved - stopping before submission. "
                              "Re-run to review again; %s is unchanged.", md_path)
        return approved

    def submit_syllabus(self, skel: dict, document_id: str | None = None) -> dict | None:
        document_id = document_id or self.cfg.document_id
        if not document_id:
            document_id = input("Enter the document id to submit to (/api/documents/syllabus-import/<id>): ").strip()
        self.cfg.document_id = document_id

        self.logger.info("=== Submitting reviewed skeleton to syllabus-import (document %s) ===", document_id)
        client = CurriculumClient(self.cfg, self.logger)
        markdown = (self.cfg.work_dir / "skeleton.md").read_text()
        result = client.import_syllabus(document_id, skel.get("technology_name", ""), markdown, skel["pillars"])
        if result is None:
            raise RuntimeError("Syllabus import failed - see log for details.")
        self.state.mark("ai_skeleton", "submitted", "done")
        return result

    def generate_content_ai(self, skel: dict):
        self.logger.info("=== Generating page content for every page via Claude ===")
        return ai_content.generate_all_content(skel, self.cfg.work_dir, self.cfg, self.logger, state=self.state)

    def run_ai(self, technology_name: str, notes: str | None = None, document_id: str | None = None):
        """
        Full PDF-free flow:
          1. Generate the skeleton with Claude.
          2. Open skeleton.md for review; stop here if not approved.
          3. Submit the approved skeleton to /api/documents/syllabus-import/<id>.
          4. Prompt for the subject id used by the tree-fetch/upload stage
             (this may be the same id as document_id, or different -
             depends on how your backend wires imports to subjects).
          5. Generate full content for every page via Claude.
          6. Upload each page's content via the existing uploader.py stage.
        """
        skel = self.generate_skeleton_ai(technology_name, notes=notes)

        if not self.review_skeleton():
            return None

        self.submit_syllabus(skel, document_id=document_id)

        self.prompt_for_subject_id()

        self.generate_content_ai(skel)
        self.upload()

        self.logger.info("AI pipeline complete for '%s'.", technology_name)
        return skel
