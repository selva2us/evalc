"""
api_client.py
=============
All network calls to the curriculum platform, consolidated in one place
(the Ruby version scattered these across migrate_content.rb, upload_content.rb,
and module_visual.rb, each redefining upload_image/headers). Uses `requests`
with retries and consistent error logging.
"""
from __future__ import annotations

import mimetypes
from pathlib import Path

import requests
from requests.adapters import HTTPAdapter, Retry


def _session() -> requests.Session:
    s = requests.Session()
    retries = Retry(total=3, backoff_factor=1.0, status_forcelist=[429, 500, 502, 503, 504])
    s.mount("https://", HTTPAdapter(max_retries=retries))
    s.mount("http://", HTTPAdapter(max_retries=retries))
    return s


class CurriculumClient:
    def __init__(self, cfg, logger):
        self.cfg = cfg
        self.logger = logger
        self.session = _session()

    def fetch_tree(self, subject_id: str | None = None) -> dict:
        subject_id = subject_id or self.cfg.subject_id
        url = f"{self.cfg.base_url}/api/subjects/{subject_id}/tree"
        self.logger.info("Fetching subject tree from %s", url)
        resp = self.session.get(url, headers=self.cfg.headers, timeout=30)
        resp.raise_for_status()
        return resp.json()

    def upload_image(self, image_path: str | Path) -> str | None:
        image_path = Path(image_path)
        content_type = mimetypes.guess_type(image_path.name)[0] or "application/octet-stream"
        self.logger.info("Uploading image %s", image_path)
        with open(image_path, "rb") as fh:
            files = {"file": (image_path.name, fh, content_type)}
            resp = self.session.post(
                self.cfg.upload_image_url, headers=self.cfg.upload_headers, files=files, timeout=60
            )
        if not resp.ok:
            self.logger.error("Image upload failed (%s): %s", resp.status_code, resp.text[:300])
            return None
        try:
            body = resp.json()
        except ValueError:
            body = {}
        url = body.get("url") or body.get("value") or (body.get("data") or {}).get("url") or (body.get("data") or {}).get("value")
        if not url:
            self.logger.error("Image upload succeeded but no url/value found in response: %s", body)
        return url

    def post_page_content(self, page_id, payload: dict) -> bool:
        url = self.cfg.page_content_url(page_id)
        resp = self.session.post(url, headers=self.cfg.headers, json=payload, timeout=30)
        if resp.ok:
            self.logger.info("Uploaded content for page %s", page_id)
            return True
        self.logger.error("Content upload failed for page %s (%s): %s", page_id, resp.status_code, resp.text[:300])
        return False

    def put_module_overview(self, module_id, payload: dict) -> bool:
        url = self.cfg.module_overview_url(module_id)
        resp = self.session.put(url, headers=self.cfg.headers, json=payload, timeout=30)
        if resp.ok:
            self.logger.info("Overview updated for module %s", module_id)
            return True
        self.logger.error("Module overview update failed for %s (%s): %s", module_id, resp.status_code, resp.text[:300])
        return False

    # ---- Additional educational-asset submissions (optional feature set) ----
    # FAQ pages reuse the exact same endpoint/payload shape as regular page
    # content (POST /api/pages/<id>/content), so they go through
    # post_page_content() directly -- no new method needed for that one.

    def put_chapter_overview(self, chapter_id, payload: dict) -> bool:
        url = self.cfg.chapter_overview_url(chapter_id)
        resp = self.session.put(url, headers=self.cfg.headers, json=payload, timeout=30)
        if resp.ok:
            self.logger.info("Overview updated for chapter %s", chapter_id)
            return True
        self.logger.error("Chapter overview update failed for %s (%s): %s", chapter_id, resp.status_code, resp.text[:300])
        return False

    def put_pillar_overview(self, pillar_id, payload: dict) -> bool:
        url = self.cfg.pillar_overview_url(pillar_id)
        resp = self.session.put(url, headers=self.cfg.headers, json=payload, timeout=30)
        if resp.ok:
            self.logger.info("Overview updated for pillar %s", pillar_id)
            return True
        self.logger.error("Pillar overview update failed for %s (%s): %s", pillar_id, resp.status_code, resp.text[:300])
        return False

    def put_module_flashcards(self, module_id, payload: list) -> bool:
        url = self.cfg.module_flashcards_url(module_id)
        resp = self.session.put(url, headers=self.cfg.headers, json=payload, timeout=30)
        if resp.ok:
            self.logger.info("Flashcards updated for module %s", module_id)
            return True
        self.logger.error("Flashcards update failed for %s (%s): %s", module_id, resp.status_code, resp.text[:300])
        return False

    def put_module_quiz(self, module_id, payload: dict) -> bool:
        url = self.cfg.module_quiz_url(module_id)
        resp = self.session.put(url, headers=self.cfg.headers, json=payload, timeout=30)
        if resp.ok:
            self.logger.info("Quiz updated for module %s", module_id)
            return True
        self.logger.error("Quiz update failed for %s (%s): %s", module_id, resp.status_code, resp.text[:300])
        return False

    def post_compiler_practice(self, chapter_id, payload: dict) -> bool:
        """Used for both Example Programs (programType=EXAMPLE) and
        Practice Programs (programType=PRACTICE) -- same endpoint, same
        payload shape, only that field differs."""
        url = self.cfg.compiler_practice_url(chapter_id)
        resp = self.session.post(url, headers=self.cfg.headers, json=payload, timeout=30)
        if resp.ok:
            self.logger.info("Program submitted for chapter %s", chapter_id)
            return True
        self.logger.error("Program submission failed for chapter %s (%s): %s", chapter_id, resp.status_code, resp.text[:300])
        return False

    def import_syllabus(self, document_id, technology_name: str, markdown: str, tree: list[dict]) -> dict | None:
        """
        POST the reviewed skeleton to /api/documents/syllabus-import/<document_id>.

        NOTE: the real request-body spec for this endpoint wasn't available
        when this was written, so the payload below is a reasonable best
        guess (raw markdown + the parsed tree, so the backend can use
        whichever it needs). Confirm against the actual API contract and
        adjust the `payload` dict below if the field names differ - nothing
        else in the pipeline needs to change if you do.
        """
        url = self.cfg.syllabus_import_url(document_id)
        filename = f"{technology_name.strip().lower().replace(' ', '-')}-curriculum.md" if technology_name else "curriculum.md"
        files = {"file": (filename, markdown.encode("utf-8"), "text/markdown")}
        self.logger.info("Submitting syllabus import to %s", url)
        self.logger.info("Submitting syllabus import to %s", self.cfg.upload_headers)
        resp = self.session.post(url, headers=self.cfg.upload_headers, files=files, timeout=60)
        if not resp.ok:
            self.logger.error("Syllabus import failed (%s): %s", resp.status_code, resp.text[:500])
            return None
        try:
            body = resp.json()
        except ValueError:
            body = {}
        self.logger.info("Syllabus import succeeded for document %s", document_id)
        return body


def fetch_title_lookup(client: "CurriculumClient", work_dir, force_refresh: bool = False) -> dict:
    """title(lowercased) -> id for every pillar/module/chapter/page in the
    real subject tree, cached to work_dir/page_lookup.json. Shared by
    Review Mode, the manual Asset Studio, and the automatic full
    generator so the subject tree is only fetched once per run."""
    import json
    from pathlib import Path

    cache_path = Path(work_dir) / "page_lookup.json"
    if cache_path.exists() and not force_refresh:
        return json.loads(cache_path.read_text())
    tree = client.fetch_tree()
    lookup: dict = {}
    collect_pages(tree, lookup)
    cache_path.write_text(json.dumps(lookup, indent=2))
    return lookup


def collect_pages(node, lookup: dict):
    """Recursively walk the subject tree, mapping normalized title -> id,
    same behavior as migrate_content.rb's collect_pages."""
    if isinstance(node, dict):
        if node.get("id") and node.get("title"):
            lookup[node["title"].strip().lower()] = node["id"]
        for value in node.values():
            if isinstance(value, list):
                for child in value:
                    collect_pages(child, lookup)
            elif isinstance(value, dict):
                collect_pages(value, lookup)
    return lookup
