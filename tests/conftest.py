"""Pytest bootstrap: never auto-migrate a real database during tests.

api/main.py runs Alembic migrations on startup (lifespan). Tests use
sqlite :memory: fixtures + get_db overrides, so the runner must be disabled
here — otherwise the repo-root .env DATABASE_URL would point migrations at
the data_synth dev database.
"""

import os

os.environ.setdefault("AUTO_MIGRATE_ON_START", "0")
