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
from api.models import mcp_skill  # noqa: F401  (registers kb_mcp_server / kb_skill)
from api.models import websearch  # noqa: F401  (registers kb_websearch_provider)
from api.models import ontology  # noqa: F401  (registers ontology_schema / kb_ontology_schema)
from api.models import orchestration  # noqa: F401  (registers kb_step_define / kb_tape / kb_tape_step)

__all__ = ["framework", "knowledge", "chat_session", "model", "mcp_skill", "websearch", "ontology", "orchestration"]
