"""Tests for the embedding service client."""

from __future__ import annotations

import json
import struct
from typing import Any
from unittest.mock import MagicMock, patch

import pytest

from phdb.embed_service import MAX_RETRIES, EmbedClient
from phdb.settings import EmbeddingSettings

# ---- Existing tests ----


def test_from_settings() -> None:
    settings_obj = type("FakeSettings", (), {
        "embedding": EmbeddingSettings(
            model="test-model", dim=384, endpoint="http://test:1234"
        )
    })()
    client = EmbedClient.from_settings(settings_obj)
    assert client.model == "test-model"
    assert client.dim == 384
    assert client.endpoint == "http://test:1234"


def test_from_settings_no_embedding() -> None:
    settings_obj = type("FakeSettings", (), {})()
    client = EmbedClient.from_settings(settings_obj)
    assert client.model == "nomic-embed-text"
    assert client.dim == 768


def test_vector_packing_shape() -> None:
    dim = 4
    vec = [0.1, 0.2, 0.3, 0.4]
    packed = struct.pack(f"{dim}f", *vec)
    assert len(packed) == dim * 4
    unpacked = struct.unpack(f"{dim}f", packed)
    for a, b in zip(vec, unpacked, strict=True):
        assert abs(a - b) < 1e-6


# ---- Mock helpers ----


def _fake_response(data: dict[str, Any], status: int = 200) -> MagicMock:
    body = json.dumps(data).encode("utf-8")
    mock = MagicMock()
    mock.read.return_value = body
    mock.__enter__ = MagicMock(return_value=mock)
    mock.__exit__ = MagicMock(return_value=False)
    mock.status = status
    return mock


# ---- Batch embedding tests ----


class TestEmbedBatch:
    def test_batch_returns_correct_count(self) -> None:
        dim = 4
        client = EmbedClient(endpoint="http://fake:1234", dim=dim)
        vecs = [[0.1] * dim, [0.2] * dim]
        with patch("phdb.embed_service.urllib.request.urlopen") as mock_open:
            mock_open.return_value = _fake_response({"embeddings": vecs})
            result = client.embed_batch(["hello", "world"])
        assert len(result) == 2
        assert result[0] == [0.1] * dim

    def test_batch_single_embedding_key(self) -> None:
        dim = 4
        client = EmbedClient(endpoint="http://fake:1234", dim=dim)
        vec = [0.3] * dim
        with patch("phdb.embed_service.urllib.request.urlopen") as mock_open:
            mock_open.return_value = _fake_response({"embedding": vec})
            result = client.embed_batch(["solo"])
        assert len(result) == 1
        assert result[0] == vec

    def test_batch_fallback_on_404(self) -> None:
        import urllib.error

        dim = 4
        client = EmbedClient(endpoint="http://fake:1234", dim=dim)
        vec = [0.5] * dim

        def side_effect(req: Any, timeout: Any = None) -> MagicMock:
            url = req.full_url if hasattr(req, "full_url") else str(req)
            if url.endswith("/api/embed"):
                raise urllib.error.HTTPError(url, 404, "Not Found", {}, None)  # type: ignore[arg-type]
            return _fake_response({"embedding": vec})

        with (
            patch("phdb.embed_service.urllib.request.urlopen", side_effect=side_effect),
            patch("phdb.embed_service.time.sleep"),
        ):
            result = client.embed_batch(["test"])
        assert len(result) == 1
        assert result[0] == vec

    def test_batch_retries_on_timeout(self) -> None:
        import urllib.error

        dim = 4
        client = EmbedClient(endpoint="http://fake:1234", dim=dim)
        vecs = [[0.1] * dim]

        call_count = 0

        def side_effect(req: Any, timeout: Any = None) -> MagicMock:
            nonlocal call_count
            call_count += 1
            if call_count <= 2:
                raise urllib.error.URLError("Connection refused")
            return _fake_response({"embeddings": vecs})

        with (
            patch("phdb.embed_service.urllib.request.urlopen", side_effect=side_effect),
            patch("phdb.embed_service.time.sleep"),
        ):
            result = client.embed_batch(["test"])
        assert len(result) == 1
        assert call_count == 3

    def test_batch_raises_after_max_retries(self) -> None:
        import urllib.error

        client = EmbedClient(endpoint="http://fake:1234")

        with (
            patch("phdb.embed_service.urllib.request.urlopen") as mock_open,
            patch("phdb.embed_service.time.sleep"),
            pytest.raises(RuntimeError, match="failed after"),
        ):
            mock_open.side_effect = urllib.error.URLError("refused")
            client.embed_batch(["test"])
        assert mock_open.call_count == MAX_RETRIES


# ---- Health check tests ----


class TestHealthCheck:
    def test_reachable(self) -> None:
        client = EmbedClient(endpoint="http://fake:1234")
        data = {"models": [{"name": "nomic-embed-text:latest"}, {"name": "llama3:latest"}]}
        with patch("phdb.embed_service.urllib.request.urlopen") as mock_open:
            mock_open.return_value = _fake_response(data)
            ok, models = client.health_check()
        assert ok is True
        assert "nomic-embed-text:latest" in models

    def test_unreachable(self) -> None:
        import urllib.error

        client = EmbedClient(endpoint="http://fake:1234")
        with patch("phdb.embed_service.urllib.request.urlopen") as mock_open:
            mock_open.side_effect = urllib.error.URLError("refused")
            ok, models = client.health_check()
        assert ok is False
        assert models == []


# ---- Verify dim tests ----


class TestVerifyDim:
    def test_returns_expected_dim(self) -> None:
        dim = 768
        client = EmbedClient(endpoint="http://fake:1234", dim=dim)
        with patch("phdb.embed_service.urllib.request.urlopen") as mock_open:
            mock_open.return_value = _fake_response(
                {"embeddings": [[0.0] * dim]}
            )
            actual = client.verify_dim()
        assert actual == dim

    def test_dim_mismatch_detected(self) -> None:
        client = EmbedClient(endpoint="http://fake:1234", dim=768)
        with patch("phdb.embed_service.urllib.request.urlopen") as mock_open:
            mock_open.return_value = _fake_response(
                {"embeddings": [[0.0] * 384]}
            )
            actual = client.verify_dim()
        assert actual == 384
        assert actual != client.dim
