"""Embedding provider protocol.

Defines the contract any embedding backend must satisfy. The framework
ships with OllamaEmbedProvider (the default). Additional providers
(OpenAI, Anthropic, local sentence-transformers) can be added by
implementing this protocol.

To swap providers at v1: change embedding.toml to point at the new
backend, then re-embed all chunks (`phdb embed --force`). Multi-dim
coexistence (querying across different embedding models without
re-embedding) is deferred to a future schema migration.
"""

from __future__ import annotations

from typing import Protocol, runtime_checkable


@runtime_checkable
class EmbedProvider(Protocol):
    """Protocol for embedding backends.

    Implementations must provide:
      - model: str — the model identifier
      - dim: int — output vector dimensionality
      - embed(text) -> bytes — single query-time embedding (packed float32)
      - embed_batch(texts, prefix) -> list[list[float]] — batch index-time embedding
      - health_check() -> (reachable, model_names) — connectivity diagnostic
      - verify_dim() -> int — probe actual output dimension
    """

    model: str
    dim: int
    endpoint: str

    def embed(self, text: str) -> bytes:
        """Embed a single query string. Returns packed float32 vector."""
        ...

    def embed_batch(
        self,
        texts: list[str],
        *,
        prefix: str = "search_document",
        timeout: int = 60,
    ) -> list[list[float]]:
        """Embed a batch of texts for indexing. Returns raw float vectors."""
        ...

    def health_check(self) -> tuple[bool, list[str]]:
        """Check if the backend is reachable. Returns (ok, available_models)."""
        ...

    def verify_dim(self) -> int:
        """Embed a probe and return actual vector dimension."""
        ...
