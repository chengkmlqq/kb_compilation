"""ORM models package.

- framework: tables ported 1:1 from the source Drizzle schema (kept tables only)
- knowledge: WeKnora-migrated tables (kb / document / chunk / embedding / wiki /
  graph) — added during the WeKnora migration phase
"""

from api.models.framework import *  # noqa: F401,F403

__all__ = ["framework"]
