import os
from datetime import timedelta


class BaseConfig:
    SECRET_KEY = os.environ.get("SECRET_KEY", "dev-secret-change-me")
    # If DATABASE_URL isn't set, SQLALCHEMY_DATABASE_URI is left unset here
    # and resolved in app/__init__.py::create_app() against the Flask app's
    # instance_path (an absolute path). A relative "sqlite:///instance/..."
    # string is resolved by SQLite against the process's current working
    # directory, not the project root, which breaks as soon as the app is
    # started from anywhere else (a different cwd, a systemd unit, a second
    # gunicorn worker, etc.) with "unable to open database file".
    SQLALCHEMY_DATABASE_URI = os.environ.get("DATABASE_URL")
    SQLALCHEMY_TRACK_MODIFICATIONS = False

    # --- Anthropic / Claude settings ---
    ANTHROPIC_API_KEY = os.environ.get("ANTHROPIC_API_KEY", "")
    ANTHROPIC_MODEL = os.environ.get("ANTHROPIC_MODEL", "claude-sonnet-5")
    ANTHROPIC_MAX_TOKENS = int(os.environ.get("ANTHROPIC_MAX_TOKENS", 8000))

    # "auto" (default): fall back to realistic demo/mock content whenever no
    # usable ANTHROPIC_API_KEY is configured, and switch back to the real
    # API automatically the moment one is -- no code changes needed either
    # direction. "on"/"off" force demo mode on or off. See
    # elluval_pipeline/demo_content.py for the shared implementation.
    DEMO_MODE = os.environ.get("DEMO_MODE", "auto")

    # --- Login (env-configured credentials; see app/auth.py) ---
    # Unset by default -- if either is blank, login is treated as "not
    # configured" and the login page shows a clear setup message instead
    # of a generic "invalid credentials" error.
    APP_USERNAME = os.environ.get("APP_USERNAME", "")
    APP_PASSWORD = os.environ.get("APP_PASSWORD", "")
    # How long a session stays logged in without activity before it
    # expires and the user has to log in again.
    PERMANENT_SESSION_LIFETIME = timedelta(
        minutes=int(os.environ.get("SESSION_LIFETIME_MINUTES", "480"))  # 8 hours
    )
    SESSION_REFRESH_EACH_REQUEST = True


class DevelopmentConfig(BaseConfig):
    DEBUG = True


class ProductionConfig(BaseConfig):
    DEBUG = False


class TestingConfig(BaseConfig):
    TESTING = True
    SQLALCHEMY_DATABASE_URI = "sqlite:///:memory:"


_CONFIGS = {
    "development": DevelopmentConfig,
    "production": ProductionConfig,
    "testing": TestingConfig,
}


def get_config(name: str | None = None):
    name = name or os.environ.get("FLASK_ENV", "development")
    return _CONFIGS.get(name, DevelopmentConfig)
