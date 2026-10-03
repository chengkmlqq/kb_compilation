"""OpenAI-compatible ASR (speech-to-text) client — POST /audio/transcriptions.

Used by the model debugger to run a real transcription probe on an uploaded
audio file. Mirrors WeKnora's ASR client surface (text + segments).
"""

from __future__ import annotations

import mimetypes
from dataclasses import dataclass

import httpx

DEFAULT_TIMEOUT = 60.0


@dataclass
class ASRConfig:
    base_url: str
    api_key: str
    model: str
    timeout: float = DEFAULT_TIMEOUT
    custom_headers: dict | None = None  # extra request headers (model-scoped)


@dataclass
class ASRResult:
    text: str
    segments: list[dict]


class ASRClient:
    """Minimal OpenAI-compatible /audio/transcriptions client."""

    def __init__(self, cfg: ASRConfig):
        self.cfg = cfg

    def transcribe(self, audio_bytes: bytes, filename: str) -> ASRResult:
        endpoint = (self.cfg.base_url or "").rstrip("/")
        if not endpoint:
            raise RuntimeError("ASR base_url is not configured")
        if not endpoint.endswith("/audio/transcriptions"):
            endpoint += "/audio/transcriptions"

        content_type = mimetypes.guess_type(filename)[0] or "application/octet-stream"
        headers = {}
        if self.cfg.api_key:
            headers["Authorization"] = f"Bearer {self.cfg.api_key}"
        if self.cfg.custom_headers:
            headers.update(self.cfg.custom_headers)

        with httpx.Client(timeout=self.cfg.timeout) as client:
            resp = client.post(
                endpoint,
                files={"file": (filename, audio_bytes, content_type)},
                data={"model": self.cfg.model},
                headers=headers,
            )
        if resp.status_code >= 300:
            raise RuntimeError(f"ASR 端点返回 {resp.status_code}: {resp.text[:300]}")
        data = resp.json()
        return ASRResult(
            text=data.get("text") or "",
            segments=data.get("segments") or [],
        )
