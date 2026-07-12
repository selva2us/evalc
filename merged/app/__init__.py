"""
Application factory for the Curriculum Generator.

Kept deliberately small and modular so new features (auth, new blueprints,
new extensions, background jobs, etc.) can be bolted on later without
touching existing code.
"""
import os

from flask import Flask
from app.config import get_config
from app.extensions import db


def create_app(config_name: str | None = None) -> Flask:
    app = Flask(__name__, instance_relative_config=True)
    app.config.from_object(get_config(config_name))

    # ---- instance folder + default DB path ---------------------------
    # Flask does NOT create instance_path automatically, and a relative
    # "sqlite:///instance/..." URI is resolved against the process's cwd
    # rather than the project root -- the combination is what causes
    # "sqlite3.OperationalError: unable to open database file" as soon as
    # the app is started from a different working directory. Fix: always
    # make sure instance_path exists, and if no DATABASE_URL was supplied,
    # build the sqlite URI from that absolute path instead of a relative
    # string.
    os.makedirs(app.instance_path, exist_ok=True)
    if not app.config.get("SQLALCHEMY_DATABASE_URI"):
        db_path = os.path.join(app.instance_path, "curriculum.db")
        app.config["SQLALCHEMY_DATABASE_URI"] = f"sqlite:///{db_path}"

    # ---- extensions -------------------------------------------------
    db.init_app(app)

    # ---- blueprints ---------------------------------------------------
    from app.routes.main import main_bp
    from app.routes.api import api_bp
    from app.routes.pipeline import pipeline_bp
    from app.routes.assets import assets_bp

    app.register_blueprint(main_bp)
    app.register_blueprint(api_bp, url_prefix="/api")
    app.register_blueprint(pipeline_bp, url_prefix="/pipeline")
    app.register_blueprint(assets_bp, url_prefix="/pipeline/assets")

    with app.app_context():
        try:
            db.create_all()
        except Exception:
            _print_db_diagnostics(app)
            raise

    return app


def _print_db_diagnostics(app: Flask) -> None:
    """On a DB open/create failure, print exactly what path SQLAlchemy
    tried to use and why it might not be writable, since sqlite3's own
    "unable to open database file" gives no detail at all."""
    import sys

    uri = app.config.get("SQLALCHEMY_DATABASE_URI", "")
    print("\n--- Database diagnostics -------------------------------------", file=sys.stderr)
    print(f"SQLALCHEMY_DATABASE_URI = {uri!r}", file=sys.stderr)
    print(f"instance_path           = {app.instance_path!r}", file=sys.stderr)
    print(f"instance_path exists    = {os.path.isdir(app.instance_path)}", file=sys.stderr)
    print(f"instance_path writable  = {os.access(app.instance_path, os.W_OK)}", file=sys.stderr)

    if uri.startswith("sqlite:///") and uri not in ("sqlite:///:memory:",):
        db_file = uri[len("sqlite:///"):]
        print(f"resolved sqlite file    = {db_file!r}", file=sys.stderr)
        print(f"  is absolute path?     = {os.path.isabs(db_file)}", file=sys.stderr)
        parent = os.path.dirname(db_file) or "."
        print(f"  parent dir            = {parent!r}", file=sys.stderr)
        print(f"  parent dir exists     = {os.path.isdir(parent)}", file=sys.stderr)
        print(f"  parent dir writable   = {os.access(parent, os.W_OK)}", file=sys.stderr)
        print(f"  file already exists   = {os.path.isfile(db_file)}", file=sys.stderr)

    print(
        "\nIf DATABASE_URL is set in your .env or shell environment, it "
        "overrides the automatic instance-path resolution above -- check "
        "for a stray/relative value there first (`grep DATABASE_URL .env`). "
        "If instance_path is not writable, fix permissions on that folder "
        "or run the app as a user that owns it.",
        file=sys.stderr,
    )
    print("----------------------------------------------------------------\n", file=sys.stderr)
