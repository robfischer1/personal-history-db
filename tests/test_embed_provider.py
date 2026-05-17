"""Tests for the EmbedProvider protocol and OllamaEmbedProvider."""
from __future__ import annotations

from phdb.embed_provider import EmbedProvider
from phdb.embed_service import EmbedClient, OllamaEmbedProvider


def test_ollama_satisfies_protocol():
    """OllamaEmbedProvider is a runtime-checkable instance of EmbedProvider."""
    client = OllamaEmbedProvider()
    assert isinstance(client, EmbedProvider)


def test_embed_client_alias():
    """EmbedClient is an alias for OllamaEmbedProvider."""
    assert EmbedClient is OllamaEmbedProvider


def test_embed_client_from_settings():
    """from_settings factory works with a mock settings object."""

    class FakeEmbedding:
        endpoint = "http://custom:11434"
        model = "custom-model"
        dim = 384

    class FakeSettings:
        embedding = FakeEmbedding()

    client = OllamaEmbedProvider.from_settings(FakeSettings())
    assert client.endpoint == "http://custom:11434"
    assert client.model == "custom-model"
    assert client.dim == 384


def test_protocol_has_required_methods():
    """Protocol defines the expected method signatures."""
    assert hasattr(EmbedProvider, "embed")
    assert hasattr(EmbedProvider, "embed_batch")
    assert hasattr(EmbedProvider, "health_check")
    assert hasattr(EmbedProvider, "verify_dim")
