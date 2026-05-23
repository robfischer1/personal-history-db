"""Tests for the clippings plugin (Phase 7 brief 025 port).

Phase 7 of the phdb Plugin Architecture plan ports clippings from the
legacy ``phdb.adapters.clippings`` module into a self-contained
``phdb.plugins.clippings`` plugin under the new contract. Per Phase 0
Q14 (no shim), the legacy import path is broken; all callers use the
plugin's ``run()`` method now.

Test file kept under the ``_adapter`` suffix for naming consistency with
the rest of the port-test suite; the contents target the new plugin.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from phdb.db import connect
from phdb.migrations.runner import MigrationRunner
from phdb.plugins.clippings import ClippingsPlugin
from phdb.settings import IdentitySettings, Settings


def _write_clipping(
    root: Path,
    name: str,
    *,
    type_value: str = "Quotation",
    url: str | None = "https://example.com/post",
    publisher: str | None = "Example Pub",
    creator: str | None = "Jane Doe",
    description: str | None = "A short description.",
    image: str | None = None,
    categories: list[str] | None = None,
    tags: list[str] | None = None,
    aliases: list[str] | None = None,
    note_type: str | None = "clipping",
    author_type: str | None = "external",
    created: str | None = "2024-01-15T10:00:00Z",
    updated: str | None = "2024-01-15T10:05:00Z",
    title: str | None = None,
    body: str = "Body prose for the clipping.\n",
) -> Path:
    """Write a clipping-shaped markdown file under *root*. Returns the path."""
    fm_lines: list[str] = ["---"]
    fm_lines.append(f'"@type": "{type_value}"')
    if title is not None:
        fm_lines.append(f"name: {title}")
    else:
        fm_lines.append(f"name: {name}")
    if url is not None:
        fm_lines.append(f"url: {url}")
    if publisher is not None:
        fm_lines.append(f"publisher: {publisher}")
    if creator is not None:
        fm_lines.append(f"creator: {creator}")
    if description is not None:
        fm_lines.append(f"description: {description}")
    if image is not None:
        fm_lines.append(f"image: {image}")
    if categories:
        fm_lines.append(f"categories: [{', '.join(categories)}]")
    if tags:
        fm_lines.append(f"tags: [{', '.join(tags)}]")
    if aliases:
        fm_lines.append(f"aliases: [{', '.join(aliases)}]")
    if note_type is not None:
        fm_lines.append(f"note_type: {note_type}")
    if author_type is not None:
        fm_lines.append(f"author_type: {author_type}")
    if created is not None:
        fm_lines.append(f"created: {created}")
    if updated is not None:
        fm_lines.append(f"updated: {updated}")
    fm_lines.append("---")
    fm_lines.append("")
    fm_lines.append(body)
    path = root / f"{name}.md"
    path.write_text("\n".join(fm_lines), encoding="utf-8")
    return path


@pytest.fixture
def clippings_root(tmp_path: Path) -> Path:
    """A fixture directory of clippings .md files mirroring the vault layout."""
    root = tmp_path / "Clippings"
    root.mkdir()
    # A folder note that must be skipped:
    (root / "Clippings.md").write_text(
        "---\n"
        '"@type": "Collection"\nname: Clippings\n---\nFolder note body.\n',
        encoding="utf-8",
    )
    # Three real clippings:
    _write_clipping(
        root, "first-clip",
        url="https://example.com/first",
        publisher="Example Pub",
        creator="Jane Doe",
        categories=["tech", "ai"],
        tags=["quote", "blog"],
    )
    _write_clipping(
        root, "second-clip",
        type_value="Comment",
        url="https://reddit.com/r/test/abc",
        publisher="reddit",
        creator="u/test",
        body="A reddit-shaped clipping body.\n",
    )
    _write_clipping(
        root, "third-clip",
        url=None,
        publisher=None,
        creator=None,
        description=None,
        body="A minimal clipping with no URL.\n",
    )
    return root


def _setup(tmp_path: Path) -> tuple[Path, Settings]:
    db_path = tmp_path / "test.db"
    with connect(db_path, create=True) as conn:
        MigrationRunner(conn).apply_pending()
    settings = Settings(
        db_path=db_path,
        identity=IdentitySettings(owner_names={"test user"}),
    )
    return db_path, settings


def _new_plugin() -> ClippingsPlugin:
    """Build a ClippingsPlugin with its in-tree manifest."""
    from phdb.core.plugin.manifest import load_manifest

    manifest_path = Path("src/phdb/plugins/clippings/plugin.toml").resolve()
    if manifest_path.exists():
        manifest = load_manifest(manifest_path)
        return ClippingsPlugin(manifest)
    return ClippingsPlugin()


class TestClippingsIntegration:
    def test_basic_ingest(self, tmp_path: Path, clippings_root: Path) -> None:
        db_path, settings = _setup(tmp_path)
        adapter = _new_plugin()
        with connect(db_path) as conn:
            report = adapter.run(clippings_root, conn, settings)
        # Three real clippings; the folder note is skipped.
        assert report.rows_yielded == 3
        assert report.rows_inserted == 3
        assert report.rows_skipped == 0

    def test_folder_note_skipped(
        self, tmp_path: Path, clippings_root: Path
    ) -> None:
        db_path, settings = _setup(tmp_path)
        adapter = _new_plugin()
        with connect(db_path) as conn:
            adapter.run(clippings_root, conn, settings)
            subjects = [
                row[0]
                for row in conn.execute("SELECT subject FROM clippings").fetchall()
            ]
        # The folder note carries name "Clippings" — it must not appear.
        assert "Clippings" not in subjects
        assert len(subjects) == 3

    def test_target_table_is_clippings(
        self, tmp_path: Path, clippings_root: Path
    ) -> None:
        db_path, settings = _setup(tmp_path)
        adapter = _new_plugin()
        with connect(db_path) as conn:
            adapter.run(clippings_root, conn, settings)
            count = conn.execute("SELECT COUNT(*) FROM clippings").fetchone()[0]
        assert count == 3

    def test_schema_type_default_quotation(
        self, tmp_path: Path, clippings_root: Path
    ) -> None:
        db_path, settings = _setup(tmp_path)
        adapter = _new_plugin()
        with connect(db_path) as conn:
            adapter.run(clippings_root, conn, settings)
            row = conn.execute(
                "SELECT schema_type FROM clippings WHERE subject = 'first-clip'"
            ).fetchone()
        assert row is not None
        assert row[0] == "Quotation"

    def test_schema_type_comment_for_reddit(
        self, tmp_path: Path, clippings_root: Path
    ) -> None:
        db_path, settings = _setup(tmp_path)
        adapter = _new_plugin()
        with connect(db_path) as conn:
            adapter.run(clippings_root, conn, settings)
            row = conn.execute(
                "SELECT schema_type FROM clippings WHERE subject = 'second-clip'"
            ).fetchone()
        assert row is not None
        assert row[0] == "Comment"

    def test_url_persisted(
        self, tmp_path: Path, clippings_root: Path
    ) -> None:
        db_path, settings = _setup(tmp_path)
        adapter = _new_plugin()
        with connect(db_path) as conn:
            adapter.run(clippings_root, conn, settings)
            row = conn.execute(
                "SELECT url FROM clippings WHERE subject = 'first-clip'"
            ).fetchone()
        assert row is not None
        assert row[0] == "https://example.com/first"

    def test_missing_url_is_null(
        self, tmp_path: Path, clippings_root: Path
    ) -> None:
        db_path, settings = _setup(tmp_path)
        adapter = _new_plugin()
        with connect(db_path) as conn:
            adapter.run(clippings_root, conn, settings)
            row = conn.execute(
                "SELECT url FROM clippings WHERE subject = 'third-clip'"
            ).fetchone()
        assert row is not None
        assert row[0] is None

    def test_bucket_is_clippings(
        self, tmp_path: Path, clippings_root: Path
    ) -> None:
        db_path, settings = _setup(tmp_path)
        adapter = _new_plugin()
        with connect(db_path) as conn:
            adapter.run(clippings_root, conn, settings)
            buckets = {
                row[0]
                for row in conn.execute(
                    "SELECT DISTINCT bucket FROM clippings"
                ).fetchall()
            }
        assert buckets == {"Clippings"}

    def test_body_text_source(
        self, tmp_path: Path, clippings_root: Path
    ) -> None:
        db_path, settings = _setup(tmp_path)
        adapter = _new_plugin()
        with connect(db_path) as conn:
            adapter.run(clippings_root, conn, settings)
            sources = {
                row[0]
                for row in conn.execute(
                    "SELECT DISTINCT body_text_source FROM clippings"
                ).fetchall()
            }
        assert sources == {"clipping-md-verbatim"}

    def test_categories_persisted_as_json(
        self, tmp_path: Path, clippings_root: Path
    ) -> None:
        db_path, settings = _setup(tmp_path)
        adapter = _new_plugin()
        with connect(db_path) as conn:
            adapter.run(clippings_root, conn, settings)
            row = conn.execute(
                "SELECT categories, tags FROM clippings WHERE subject = 'first-clip'"
            ).fetchone()
        assert row is not None
        # Both written as JSON-encoded arrays by the format parser.
        assert row[0] is not None and "tech" in row[0]
        assert row[1] is not None and "quote" in row[1]

    def test_file_path_relative(
        self, tmp_path: Path, clippings_root: Path
    ) -> None:
        db_path, settings = _setup(tmp_path)
        adapter = _new_plugin()
        with connect(db_path) as conn:
            adapter.run(clippings_root, conn, settings)
            paths = {
                row[0]
                for row in conn.execute(
                    "SELECT file_path FROM clippings"
                ).fetchall()
            }
        # Stored relative to the clippings root.
        assert "first-clip.md" in paths
        assert "second-clip.md" in paths
        assert "third-clip.md" in paths

    def test_idempotent_rerun(
        self, tmp_path: Path, clippings_root: Path
    ) -> None:
        db_path, settings = _setup(tmp_path)
        adapter = _new_plugin()
        with connect(db_path) as conn:
            adapter.run(clippings_root, conn, settings)
        adapter2 = _new_plugin()
        with connect(db_path) as conn:
            r2 = adapter2.run(clippings_root, conn, settings)
        assert r2.rows_inserted == 0
        assert r2.rows_skipped == r2.rows_yielded

    def test_source_file_registered(
        self, tmp_path: Path, clippings_root: Path
    ) -> None:
        db_path, settings = _setup(tmp_path)
        adapter = _new_plugin()
        with connect(db_path) as conn:
            report = adapter.run(clippings_root, conn, settings)
        assert report.source_file_id > 0
        with connect(db_path) as conn:
            row = conn.execute(
                "SELECT source_kind, file_kind FROM source_files WHERE id = ?",
                (report.source_file_id,),
            ).fetchone()
        assert row is not None
        assert row[0] == "vault-clippings"
        assert row[1] == "md"

    def test_discover_yields_directory(
        self, tmp_path: Path, clippings_root: Path
    ) -> None:
        adapter = _new_plugin()
        results = list(adapter.discover(clippings_root))
        assert len(results) == 1
        path, kind = results[0]
        assert path == clippings_root
        assert kind == "vault-clippings"

    def test_discover_skips_empty_directory(self, tmp_path: Path) -> None:
        empty = tmp_path / "empty"
        empty.mkdir()
        adapter = _new_plugin()
        assert list(adapter.discover(empty)) == []
