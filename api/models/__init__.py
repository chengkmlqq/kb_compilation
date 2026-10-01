"""ORM models package.

Two declarative bases, two stores:
- Base (framework): framework tables ported 1:1 from the source Drizzle
  schema, shared with the legacy database
- KnowledgeBase (knowledge domain): wiki/kb tables on the PG + pgvector store
"""

from api.models.framework import *  # noqa: F401,F403
from api.models import knowledge  # noqa: F401  (registers knowledge models)
from api.models import chat_session  # noqa: F401  (registers chat session/message models)
from api.models import model  # noqa: F401  (registers scoped model registry kb_model)

__all__ = ["framework", "knowledge", "chat_session", "model"]
