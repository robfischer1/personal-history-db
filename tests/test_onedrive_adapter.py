"""Tests for the onedrive adapter."""

from __future__ import annotations

from pathlib import Path

from phdb.adapters.onedrive import (
    OneDriveAdapter,
    _derive_bucket,
    _is_reference_body_allowed,
)
from phdb.db import connect
from phdb.migrations.runner import MigrationRunner
from phdb.settings import IdentitySettings, Settings

FIXTURE_DIR = Path(__file__).parent / "fixtures" / "onedrive"


def _setup(tmp_path: Path) -> tuple[Path, Settings]:
    db_path = tmp_path / "test.db"
    with connect(db_path, create=True) as conn:
        MigrationRunner(conn).apply_pending()
    settings = Settings(
        db_path=db_path,
        identity=IdentitySettings(owner_names={"test user"}),
    )
    return db_path, settings


class TestDeriveBucket:
    def test_single_part(self) -> None:
        assert _derive_bucket(("Outputs",)) == "Outputs"

    def test_two_parts(self) -> None:
        assert _derive_bucket(("Outputs", "Projects")) == "Outputs/Projects"

    def test_deep_path(self) -> None:
        assert _derive_bucket(("Reference", "Mind Tools", "deep")) == "Reference/Mind Tools"

    def test_empty(self) -> None:
        assert _derive_bucket(()) == "(root)"


class TestReferenceBodyAllowlist:
    def test_outputs_always_allowed(self) -> None:
        assert _is_reference_body_allowed(("Outputs", "Projects", "file.txt"))

    def test_records_always_allowed(self) -> None:
        assert _is_reference_body_allowed(("Records", "file.txt"))

    def test_reference_allowlisted_subdir(self) -> None:
        assert _is_reference_body_allowed(("Reference", "Mind Tools", "file.txt"))

    def test_reference_non_allowlisted_subdir(self) -> None:
        assert not _is_reference_body_allowed(("Reference", "Downloaded Library", "file.txt"))


class TestOneDriveIntegration:
    def test_basic_ingest(self, tmp_path: Path) -> None:
        db_path, settings = _setup(tmp_path)
        adapter = OneDriveAdapter()
        with connect(db_path) as conn:
            report = adapter.run(FIXTURE_DIR, conn, settings)
        # Outputs/Projects/hello.txt, Reference/Mind Tools/data.json, Records/notes.md
        # Reference/Downloaded Library/ebook.txt → metadata-only (body=None, is_bulk=1)
        assert report.rows_inserted == 4

    def test_schema_type(self, tmp_path: Path) -> None:
        db_path, settings = _setup(tmp_path)
        adapter = OneDriveAdapter()
        with connect(db_path) as conn:
            adapter.run(FIXTURE_DIR, conn, settings)
            types = conn.execute("SELECT DISTINCT schema_type FROM documents").fetchall()
        assert all(t[0] == "DigitalDocument" for t in types)

    def test_target_table_is_documents(self, tmp_path: Path) -> None:
        db_path, settings = _setup(tmp_path)
        adapter = OneDriveAdapter()
        with connect(db_path) as conn:
            adapter.run(FIXTURE_DIR, conn, settings)
            doc_count = conn.execute("SELECT COUNT(*) FROM documents").fetchone()[0]
        assert doc_count == 4

    def test_reference_is_bulk(self, tmp_path: Path) -> None:
        db_path, settings = _setup(tmp_path)
        adapter = OneDriveAdapter()
        with connect(db_path) as conn:
            adapter.run(FIXTURE_DIR, conn, settings)
            bulk_rows = conn.execute(
                "SELECT subject FROM documents WHERE is_bulk = 1"
            ).fetchall()
            non_bulk = conn.execute(
                "SELECT subject FROM documents WHERE is_bulk = 0"
            ).fetchall()
        bulk_names = {r[0] for r in bulk_rows}
        non_bulk_names = {r[0] for r in non_bulk}
        assert "data.json" in bulk_names
        assert "ebook.txt" in bulk_names
        assert "hello.txt" in non_bulk_names
        assert "notes.md" in non_bulk_names

    def test_idempotent_rerun(self, tmp_path: Path) -> None:
        db_path, settings = _setup(tmp_path)
        adapter = OneDriveAdapter()
        with connect(db_path) as conn:
            adapter.run(FIXTURE_DIR, conn, settings)
        with connect(db_path) as conn:
            r2 = OneDriveAdapter().run(FIXTURE_DIR, conn, settings)
        assert r2.rows_inserted == 0

    def test_skips_excluded_top_dirs(self, tmp_path: Path) -> None:
        """Files outside INCLUDE_TOP_DIRS should not be ingested."""
        excluded = FIXTURE_DIR / "SomeOtherDir"
        excluded.mkdir(exist_ok=True)
        secret = excluded / "excluded_secret.txt"
        secret.write_text("should not appear")
        try:
            db_path, settings = _setup(tmp_path)
            adapter = OneDriveAdapter()
            with connect(db_path) as conn:
                adapter.run(FIXTURE_DIR, conn, settings)
                subjects = [
                    r[0]
                    for r in conn.execute("SELECT subject FROM documents").fetchall()
                ]
            assert "excluded_secret.txt" not in subjects
        finally:
            secret.unlink(missing_ok=True)
            excluded.rmdir()

    def test_body_content(self, tmp_path: Path) -> None:
        db_path, settings = _setup(tmp_path)
        adapter = OneDriveAdapter()
        with connect(db_path) as conn:
            adapter.run(FIXTURE_DIR, conn, settings)
            row = conn.execute(
                "SELECT body_text FROM documents WHERE subject = 'hello.txt'"
            ).fetchone()
        assert row is not None
        assert "Hello from OneDrive" in row[0]

    def test_bucket_populated(self, tmp_path: Path) -> None:
        db_path, settings = _setup(tmp_path)
        adapter = OneDriveAdapter()
        with connect(db_path) as conn:
            adapter.run(FIXTURE_DIR, conn, settings)
            buckets = {b[0] for b in conn.execute(
                "SELECT DISTINCT bucket FROM documents WHERE bucket IS NOT NULL"
            ).fetchall()}
        assert "Outputs/Projects" in buckets

    def test_file_path_populated(self, tmp_path: Path) -> None:
        db_path, settings = _setup(tmp_path)
        adapter = OneDriveAdapter()
        with connect(db_path) as conn:
            adapter.run(FIXTURE_DIR, conn, settings)
            paths = conn.execute(
                "SELECT file_path FROM documents WHERE file_path IS NOT NULL"
            ).fetchall()
        assert len(paths) == 4

    def test_non_allowlisted_reference_metadata_only(self, tmp_path: Path) -> None:
        db_path, settings = _setup(tmp_path)
        adapter = OneDriveAdapter()
        with connect(db_path) as conn:
            adapter.run(FIXTURE_DIR, conn, settings)
            row = conn.execute(
                "SELECT body_text, is_bulk FROM documents WHERE subject = 'ebook.txt'"
            ).fetchone()
        assert row is not None
        assert row[0] is None
        assert row[1] == 1

    def test_no_threads_created(self, tmp_path: Path) -> None:
        db_path, settings = _setup(tmp_path)
        adapter = OneDriveAdapter()
        with connect(db_path) as conn:
            report = adapter.run(FIXTURE_DIR, conn, settings)
        assert report.threads_created == 0
