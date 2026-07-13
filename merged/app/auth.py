"""
auth.py
=======
Simple session-based authentication gate for the whole app.

Credentials come from environment variables (APP_USERNAME / APP_PASSWORD
-- see app/config.py), never hardcoded in source. A single before_request
hook (registered on the app object in app/__init__.py, not on individual
blueprints) protects every existing route without those routes needing
any changes: no view function, template, or API response shape changed
because of this feature.

Session behavior:
  - On successful login, session["authenticated_user"] is set and the
    session is marked permanent, so it expires after
    PERMANENT_SESSION_LIFETIME (default 8 hours, see app/config.py)
    of inactivity rather than only on browser close.
  - Logout clears the session outright.
  - If APP_USERNAME/APP_PASSWORD aren't configured at all, the login page
    says so explicitly instead of pretending any credentials are wrong --
    this is a setup/configuration state, not a login failure.
"""
from __future__ import annotations

import hmac

from flask import Blueprint, current_app, flash, redirect, render_template, request, session, url_for

auth_bp = Blueprint("auth", __name__)

SESSION_KEY = "authenticated_user"

# Endpoints reachable without being logged in. Flask's built-in static
# file route is named "static"; everything else in the app goes through
# the auth gate.
PUBLIC_ENDPOINTS = {"auth.login"}


def _configured_credentials() -> tuple[str, str]:
    return (
        current_app.config.get("APP_USERNAME", "") or "",
        current_app.config.get("APP_PASSWORD", "") or "",
    )


def login_is_configured() -> bool:
    username, password = _configured_credentials()
    return bool(username) and bool(password)


def is_authenticated() -> bool:
    return bool(session.get(SESSION_KEY))


def require_login() -> "flask.Response | None":
    """Registered as an app-wide before_request hook in app/__init__.py.
    Returning None lets the request proceed unchanged; returning a
    redirect stops it there.

    Opt-in by configuration: if APP_USERNAME/APP_PASSWORD haven't been
    set, the whole gate is skipped and every route behaves exactly as it
    did before this feature existed -- so upgrading the app doesn't lock
    out an existing deployment that hasn't decided to turn login on yet.
    Configuring both env vars is what "explicitly uses" this feature.
    """
    if not login_is_configured():
        return None
    if request.endpoint is None:
        return None
    if request.endpoint == "static" or request.endpoint in PUBLIC_ENDPOINTS:
        return None
    if is_authenticated():
        return None

    if request.method == "GET":
        next_url = request.full_path if request.query_string else request.path
        return redirect(url_for("auth.login", next=next_url))
    # Non-GET requests (form posts, API calls) to a protected endpoint
    # while logged out: no sensible "next" page to bounce back to, and no
    # body/CSRF context to forward, so just send them to log in.
    return redirect(url_for("auth.login"))


@auth_bp.route("/login", methods=["GET", "POST"])
def login():
    if is_authenticated():
        return redirect(url_for("main.index"))

    if request.method == "POST":
        if not login_is_configured():
            flash(
                "Login isn't configured yet. Set APP_USERNAME and APP_PASSWORD "
                "in your environment (or .env file) and restart the app.",
                "error",
            )
        else:
            username = request.form.get("username", "")
            password = request.form.get("password", "")
            expected_user, expected_pass = _configured_credentials()
            # hmac.compare_digest for constant-time comparison (avoids
            # leaking match-length via response timing).
            user_ok = hmac.compare_digest(username, expected_user)
            pass_ok = hmac.compare_digest(password, expected_pass)
            if user_ok and pass_ok:
                session.clear()
                session[SESSION_KEY] = username
                session.permanent = True
                next_url = request.form.get("next") or request.args.get("next") or url_for("main.index")
                return redirect(next_url)
            flash("Invalid username or password.", "error")

    return render_template(
        "auth/login.html",
        login_configured=login_is_configured(),
        next_url=request.args.get("next", ""),
    )


@auth_bp.route("/logout", methods=["POST", "GET"])
def logout():
    session.clear()
    flash("You have been logged out.", "success")
    return redirect(url_for("auth.login"))
