"""Embedding client for Ollama-compatible endpoints.

Implements the EmbedProvider protocol. This is the default (and currently
only) provider shipped with phdb. See embed_provider.py for the protocol
definition and guidance on adding new providers.
"""

from __future__ import annotations

import json
import struct
import time
import urllib.error
import urllib.request
from dataclasses import dataclass

MAX_RETRIES: int = 5
RETRY_BACKOFF_S: float = 2.0
HTTP_TIMEOUT_S: int = 60


@dataclass
class OllamaEmbedProvider:
    """Ollama-compatible embedding provider.

    Implements the EmbedProvider protocol for any Ollama-compatible
    /api/embed endpoint (Ollama, llama.cpp server, etc.).
    """

    endpoint: str = "http://localhost:11434"
    model: str = "nomic-embed-text"
    dim: int = 768

    def embed(self, text: str) -> bytes:
        """Embed a query string, return packed float32 vector."""
        body = json.dumps(
            {"model": self.model, "input": [f"search_query: {text}"]}
        ).encode()
        req = urllib.request.Request(
            f"{self.endpoint}/api/embed",
            data=body,
            headers={"Content-Type": "application/json"},
        )
        with urllib.request.urlopen(req, timeout=30) as r:
            payload = json.loads(r.read().decode())
            emb = payload.get("embeddings", [payload.get("embedding")])[0]
        return struct.pack(f"{self.dim}f", *emb)

    def embed_batch(
        self,
        texts: list[str],
        *,
        prefix: str = "search_document",
        timeout: int = HTTP_TIMEOUT_S,
    ) -> list[list[float]]:
        """Embed a batch of texts via Ollama /api/embed.

        Falls back to /api/embeddings (single) on 404 (old Ollama).
        Retries with exponential backoff on transient errors.
        """
        prompts = [f"{prefix}: {t}" for t in texts]
        body = json.dumps({"model": self.model, "input": prompts}).encode("utf-8")
        req = urllib.request.Request(
            f"{self.endpoint}/api/embed",
            data=body,
            headers={"Content-Type": "application/json"},
        )
        last_err: BaseException | None = None
        for attempt in range(MAX_RETRIES):
            try:
                with urllib.request.urlopen(req, timeout=timeout) as resp:
                    payload = json.loads(resp.read().decode("utf-8"))
                    if "embeddings" in payload:
                        result: list[list[float]] = payload["embeddings"]
                        return result
                    if "embedding" in payload:
                        single: list[float] = payload["embedding"]
                        return [single]
                    msg = f"Unexpected Ollama response keys: {list(payload.keys())}"
                    raise RuntimeError(msg)
            except urllib.error.HTTPError as e:
                if e.code == 404:
                    return [
                        self._embed_single_fallback(p, timeout=timeout)
                        for p in prompts
                    ]
                last_err = e
            except (urllib.error.URLError, TimeoutError, OSError) as e:
                last_err = e
            time.sleep(RETRY_BACKOFF_S * (2**attempt))
        msg = f"Ollama batch embed failed after {MAX_RETRIES} retries: {last_err}"
        raise RuntimeError(msg)

    def _embed_single_fallback(
        self, prompt: str, *, timeout: int = HTTP_TIMEOUT_S
    ) -> list[float]:
        """Single-prompt fallback for old Ollama without batch endpoint."""
        body = json.dumps({"model": self.model, "prompt": prompt}).encode("utf-8")
        req = urllib.request.Request(
            f"{self.endpoint}/api/embeddings",
            data=body,
            headers={"Content-Type": "application/json"},
        )
        last_err: BaseException | None = None
        for attempt in range(MAX_RETRIES):
            try:
                with urllib.request.urlopen(req, timeout=timeout) as resp:
                    payload = json.loads(resp.read().decode("utf-8"))
                    vec: list[float] = payload["embedding"]
                    return vec
            except (urllib.error.URLError, TimeoutError, OSError) as e:
                last_err = e
                time.sleep(RETRY_BACKOFF_S * (2**attempt))
        msg = f"Ollama single embed failed after {MAX_RETRIES} retries: {last_err}"
        raise RuntimeError(msg)

    def health_check(self) -> tuple[bool, list[str]]:
        """Ping Ollama /api/tags. Returns (reachable, model_names)."""
        try:
            with urllib.request.urlopen(
                f"{self.endpoint}/api/tags", timeout=5
            ) as resp:
                tags = json.loads(resp.read().decode("utf-8"))
                models = [m.get("name", "") for m in tags.get("models", [])]
                return True, models
        except (urllib.error.URLError, TimeoutError, OSError):
            return False, []

    def verify_dim(self) -> int:
        """Embed a probe text and return the actual vector dimension."""
        vecs = self.embed_batch(["dimension probe"], prefix="search_document")
        return len(vecs[0])

    @classmethod
    def from_settings(cls, settings: object) -> OllamaEmbedProvider:
        """Build from a Settings object's embedding sub-config."""
        embedding = getattr(settings, "embedding", None)
        if embedding is None:
            return cls()
        return cls(
            endpoint=getattr(embedding, "endpoint", "http://localhost:11434"),
            model=getattr(embedding, "model", "nomic-embed-text"),
            dim=getattr(embedding, "dim", 768),
        )


# Backwards-compatible alias — existing code imports EmbedClient
EmbedClient = OllamaEmbedProvider
