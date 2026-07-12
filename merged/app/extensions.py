"""
Shared extension instances.

Kept in their own module (separate from `app/__init__.py` and `app/models.py`)
so both can import them without circular-import issues. Add future extensions
(e.g. Flask-Migrate, Flask-Login, Flask-Caching) here.
"""
from flask_sqlalchemy import SQLAlchemy

db = SQLAlchemy()
