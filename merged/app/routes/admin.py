"""
admin.py
========
Post-login administrative pages:
  - /admin/settings  environment/runtime configuration (Anthropic keys,
                     models, curriculum backend URL/token, ...)
  - /admin/cookies   Cloudflare cookie management (CF_AppSession,
                     CF_Authorization, cf_clearance)
  - /admin/prompts   view/edit the centralized system prompt files

All three are protected the same way every other route in the app is --
the app-wide require_login() before_request hook (see app/auth.py and
app/__init__.py) -- so no extra @login_required-style decorator is
needed on the view functions below.

Nothing here touches the existing Architect tool / AI Pipeline / Asset
Studio routes, models, or generation code -- this blueprint only manages
*configuration* those other parts already read.
"""
from __future__ import annotations

from flask import Blueprint, current_app, flash, redirect, render_template, request, url_for

from app.services.settings_service import (
    SettingValidationError,
    clear_setting,
    get_current_settings,
    update_setting,
)
from elluval_pipeline.cookie_store import SUPPORTED_COOKIES
from elluval_pipeline.cookie_store import clear_all as clear_all_cookies
from elluval_pipeline.cookie_store import delete_cookie, get_status, set_cookie
from elluval_pipeline.prompts import PROMPTS_DIR, clear_cache, list_prompts

admin_bp = Blueprint("admin", __name__)


# ---------------------------------------------------------------------
# Environment / settings management
# ---------------------------------------------------------------------
@admin_bp.route("/settings", methods=["GET"])
def settings():
    grouped: dict[str, list[dict]] = {}
    for row in get_current_settings():
        grouped.setdefault(row["group"], []).append(row)
    return render_template("admin/settings.html", grouped_settings=grouped)


@admin_bp.route("/settings/update", methods=["POST"])
def settings_update():
    key = request.form.get("key", "")
    value = request.form.get("value", "")
    try:
        update_setting(current_app, key, value)
        flash(f"{key} updated.", "success")
    except SettingValidationError as exc:
        flash(str(exc), "error")
    return redirect(url_for("admin.settings"))


@admin_bp.route("/settings/clear", methods=["POST"])
def settings_clear():
    key = request.form.get("key", "")
    try:
        clear_setting(current_app, key)
        flash(f"{key} cleared.", "success")
    except SettingValidationError as exc:
        flash(str(exc), "error")
    return redirect(url_for("admin.settings"))


# ---------------------------------------------------------------------
# Cookie management
# ---------------------------------------------------------------------
@admin_bp.route("/cookies", methods=["GET"])
def cookies():
    return render_template(
        "admin/cookies.html", cookie_status=get_status(), supported_cookies=SUPPORTED_COOKIES
    )


@admin_bp.route("/cookies/update", methods=["POST"])
def cookies_update():
    name = request.form.get("name", "")
    value = request.form.get("value", "")
    try:
        set_cookie(name, value)
        flash(f"{name} saved.", "success")
    except ValueError as exc:
        flash(str(exc), "error")
    return redirect(url_for("admin.cookies"))


@admin_bp.route("/cookies/delete", methods=["POST"])
def cookies_delete():
    name = request.form.get("name", "")
    delete_cookie(name)
    flash(f"{name} deleted.", "success")
    return redirect(url_for("admin.cookies"))


@admin_bp.route("/cookies/clear-all", methods=["POST"])
def cookies_clear_all():
    clear_all_cookies()
    flash("All cookies cleared.", "success")
    return redirect(url_for("admin.cookies"))


# ---------------------------------------------------------------------
# Centralized prompt viewing/editing
# ---------------------------------------------------------------------
@admin_bp.route("/prompts", methods=["GET"])
def prompts():
    names = list_prompts()
    selected = request.args.get("name") or (names[0] if names else None)
    content = ""
    if selected and selected in names:
        content = (PROMPTS_DIR / f"{selected}.txt").read_text(encoding="utf-8")
    return render_template("admin/prompts.html", names=names, selected=selected, content=content)


@admin_bp.route("/prompts/update", methods=["POST"])
def prompts_update():
    name = request.form.get("name", "")
    content = request.form.get("content", "")
    if name not in list_prompts():
        flash("Unknown prompt file.", "error")
        return redirect(url_for("admin.prompts"))
    (PROMPTS_DIR / f"{name}.txt").write_text(content, encoding="utf-8")
    clear_cache()
    flash(f"{name}.txt saved -- takes effect on the next generation call.", "success")
    return redirect(url_for("admin.prompts", name=name))
