"""Embedding client (OpenAI-compatible /v1/embeddings).

Mirrors WeKnora's embedding channel: an OpenAI-compatible endpoint driven by
EMBEDDING_BASE_URL / EMBEDDING_API_KEY / EMBEDDING_MODEL, with batched
requests and a deterministic zero-vector fallback so ingestion never hard-fails
on a transient model outage (chunks stay searchable via the keyword arm).
"""

from __future__ import annotations

import logging
import math
import threading
import time
from dataclasses import dataclass

import httpx

from api.config import get_settings
from api.services.runtime_config import resolve_runtime_config
from sqlalchemy.orm import Session

logger = logging.getLogger(__name__)

DEFAULT_BATCH_SIZE = 16
DEFAULT_TIMEOUT = 60.0
_RETRY_STATUS = {429, 500, 502, 503, 504}


@dataclass
class EmbeddingConfig:
    base_url: str
    api_key: str
    model: str
    dim: int
    timeout: float = DEFAULT_TIMEOUT
    batch_size: int = DEFAULT_BATCH_SIZE


def load_embedding_config(
    db: Session | None = None,
    *,
    user_id: str = "",
    team_name: str = "",
    is_sys_admin: bool = False,
) -> EmbeddingConfig:
    """Resolve embedding config with scoped model priority: scoped model
    registry default (personal > team > system), then legacy DB (SYSTEM_CONFIG)
    wins over env, like the source platform's runtime-config precedence.
    Consumers with no user context resolve the system default first."""
    settings = get_settings()

    if db is not None:
        try:
            from api.services.models import resolve_model_config

            resolved = resolve_model_config(
                db,
                "embedding",
                caller_user_id=user_id or "",
                caller_team_name=team_name or "",
                is_sys_admin=is_sys_admin,
            )
            if resolved and resolved.get("base_url"):
                return EmbeddingConfig(
                    base_url=resolved["base_url"],
                    api_key=resolved.get("api_key") or "",
                    model=resolved.get("model") or "",
                    dim=int(resolved.get("dimension") or settings.EMBEDDING_DIM),
                )
        except Exception:
            logger.warning(
                "failed to resolve scoped embedding model; using legacy config", exc_info=True
            )

    def _resolve(env_key: str, db_codes: list[str], fallback: str = "") -> str:
        if db is None:
            return fallback
        try:
            return resolve_runtime_config(db, env_key, db_codes=db_codes, fallback=fallback)["value"]
        except Exception:
            logger.warning("failed to resolve %s from DB; using env", env_key, exc_info=True)
            return fallback

    base_url = _resolve("EMBEDDING_BASE_URL", ["EMBEDDING_BASE_URL", "DIFY_CHAT_API_ENDPOINT"], settings.EMBEDDING_BASE_URL)
    api_key = _resolve("EMBEDDING_API_KEY", ["EMBEDDING_API_KEY"], settings.EMBEDDING_API_KEY)
    model = _resolve("EMBEDDING_MODEL", ["EMBEDDING_MODEL", "SUMMARY_MODEL_NAME"], settings.EMBEDDING_MODEL)
    dim = int(_resolve("EMBEDDING_DIM", ["EMBEDDING_DIM"], str(settings.EMBEDDING_DIM)) or settings.EMBEDDING_DIM)
    return EmbeddingConfig(base_url=base_url, api_key=api_key, model=model, dim=dim)


def l2_normalize(vec: list[float]) -> list[float]:
    norm = math.sqrt(sum(x * x for x in vec))
    if norm == 0:
        return vec
    return [x / norm for x in vec]


def _zero_vector(dim: int) -> list[float]:
    return [0.0] * dim


class EmbeddingClient:
    """OpenAI-compatible embeddings client with retry + batch."""

    def __init__(self, cfg: EmbeddingConfig):
        self.cfg = cfg

    def _endpoint(self) -> str:
        base = (self.cfg.base_url or "").rstrip("/")
        if not base:
            raise RuntimeError("EMBEDDING_BASE_URL is not configured")
        if base.endswith("/embeddings"):
            return base
        return base + "/embeddings"

    def embed_texts(self, texts: list[str]) -> list[list[float]]:
        """Embed a batch of texts; returns one vector per input (in order)."""
        if not texts:
            return []
        out: list[list[float]] = []
        batch = max(1, self.cfg.batch_size)
        for i in range(0, len(texts), batch):
            out.extend(self._embed_batch(texts[i : i + batch]))
        return out

    def _embed_batch(self, texts: list[str]) -> list[list[float]]:
        payload = {"model": self.cfg.model, "input": texts}
        headers = {"Content-Type": "application/json"}
        if self.cfg.api_key:
            headers["Authorization"] = f"Bearer {self.cfg.api_key}"

        last_err: Exception | None = None
        for attempt in range(3):
            try:
                with httpx.Client(timeout=self.cfg.timeout) as client:
                    resp = client.post(self._endpoint(), json=payload, headers=headers)
                if resp.status_code in _RETRY_STATUS:
                    last_err = RuntimeError(f"embedding endpoint {resp.status_code}: {resp.text[:200]}")
                    time.sleep(1.0 * (attempt + 1))
                    continue
                resp.raise_for_status()
                data = resp.json()
                vectors = [item["embedding"] for item in data["data"]]
                return [l2_normalize(v) for v in vectors]
            except Exception as e:  # noqa: BLE001
                last_err = e
                time.sleep(1.0 * (attempt + 1))
        # Degrade gracefully: keep chunks but leave them un-embedded (keyword arm still works).
        logger.error("embedding batch failed after retries: %s", last_err)
        return [_zero_vector(self.cfg.dim) for _ in texts]

    def embed_query(self, text: str) -> list[float] | None:
        """Embed a single query; None when the model is unavailable."""
        if not text or not text.strip():
            return None
        try:
            vectors = self.embed_texts([text])
        except Exception:
            logger.exception("embed_query failed")
            return None
        if not vectors:
            return None
        vec = vectors[0]
        if all(x == 0.0 for x in vec):
            return None
        return vec


_client_lock = threading.Lock()
_clients: dict[tuple, EmbeddingClient] = {}


def get_embedding_client(
    db: Session | None = None,
    *,
    user_id: str = "",
    team_name: str = "",
    is_sys_admin: bool = False,
) -> EmbeddingClient:
    """Process-wide client cache keyed by resolution context.

    Scoped model configs (personal/team/system) can resolve different
    endpoints per caller, so the cache key is (user_id, team_name,
    is_sys_admin) instead of a single global client. Reset after config
    changes via reset_embedding_client()."""
    global _clients
    key = (user_id or "", team_name or "", bool(is_sys_admin))
    with _client_lock:
        client = _clients.get(key)
        if client is None:
            cfg = load_embedding_config(
                db,
                user_id=user_id or "",
                team_name=team_name or "",
                is_sys_admin=is_sys_admin,
            )
            client = EmbeddingClient(cfg)
            _clients = dict(_clients)
            _clients[key] = client
        return client


def reset_embedding_client() -> None:
    """Clear the cached clients (after config changes / in tests)."""
    global _clients
    with _client_lock:
        _clients = {}
