"""Tests for adapter discovery and external adapter loading."""

from __future__ import annotations

from pathlib import Path

from phdb.adapters.base import Adapter
from phdb.adapters.loader import discover_adapters


def test_discovers_calendar_adapter() -> None:
    """The built-in calendar adapter should be discoverable from the adapters package."""
    import phdb.adapters.calendar  # noqa: F401

    adapters_dir = Path(phdb.adapters.calendar.__file__).parent
    registry = discover_adapters([adapters_dir])
    assert "calendar" in registry


def test_discovers_external_adapter(tmp_path: Path) -> None:
    """An external .py file containing an Adapter subclass should be discovered."""
    ext_file = tmp_path / "my_custom_adapter.py"
    ext_file.write_text(
        """\
from phdb.adapters.base import Adapter, AdapterRow, DedupStrategy
from collections.abc import Iterator
from pathlib import Path

class CustomAdapter(Adapter):
    name = "custom_test"
    source_kind = "test"
    file_kind = "csv"
    schema_type = "Message"
    dedup_strategy = DedupStrategy.CONTENT_HASH

    def iter_rows(self, source_path: Path, **kwargs) -> Iterator[AdapterRow]:
        yield AdapterRow(body_text="hello", body_text_source="test")
""",
        encoding="utf-8",
    )

    registry = discover_adapters([tmp_path])
    assert "custom_test" in registry
    assert issubclass(registry["custom_test"], Adapter)


def test_instance_overrides_project(tmp_path: Path) -> None:
    """When two adapters share a name, the later path wins (instance overrides project)."""
    proj_dir = tmp_path / "project"
    inst_dir = tmp_path / "instance"
    proj_dir.mkdir()
    inst_dir.mkdir()

    adapter_code = """\
from phdb.adapters.base import Adapter, AdapterRow, DedupStrategy
from collections.abc import Iterator
from pathlib import Path

class OverrideAdapter(Adapter):
    name = "shared_name"
    source_kind = "{kind}"
    file_kind = "csv"
    schema_type = "Message"
    dedup_strategy = DedupStrategy.CONTENT_HASH

    def iter_rows(self, source_path: Path, **kwargs) -> Iterator[AdapterRow]:
        yield from []
"""

    (proj_dir / "proj_shared.py").write_text(
        adapter_code.format(kind="project"), encoding="utf-8"
    )
    (inst_dir / "inst_shared.py").write_text(
        adapter_code.format(kind="instance"), encoding="utf-8"
    )

    registry = discover_adapters([proj_dir, inst_dir])
    assert "shared_name" in registry
    assert registry["shared_name"].source_kind == "instance"


def test_skips_underscore_files(tmp_path: Path) -> None:
    """Files starting with _ should be skipped."""
    (tmp_path / "_private.py").write_text(
        """\
from phdb.adapters.base import Adapter, AdapterRow, DedupStrategy
from collections.abc import Iterator
from pathlib import Path

class PrivateAdapter(Adapter):
    name = "private"
    source_kind = "test"
    file_kind = "csv"
    schema_type = "Message"
    dedup_strategy = DedupStrategy.CONTENT_HASH

    def iter_rows(self, source_path: Path, **kwargs) -> Iterator[AdapterRow]:
        yield from []
""",
        encoding="utf-8",
    )

    registry = discover_adapters([tmp_path])
    assert "private" not in registry


def test_external_adapter_runs_end_to_end(tmp_path: Path) -> None:
    """An external adapter can be discovered and run through the full pipeline."""
    from phdb.db import connect
    from phdb.migrations.runner import MigrationRunner
    from phdb.settings import Settings

    ext_file = tmp_path / "ext_adapter.py"
    ext_file.write_text(
        """\
from phdb.adapters.base import Adapter, AdapterRow, DedupStrategy
from collections.abc import Iterator
from pathlib import Path

class ExtAdapter(Adapter):
    name = "ext_e2e"
    source_kind = "test"
    file_kind = "txt"
    schema_type = "Message"
    dedup_strategy = DedupStrategy.CONTENT_HASH

    def iter_rows(self, source_path: Path, **kwargs) -> Iterator[AdapterRow]:
        for i, line in enumerate(source_path.read_text().splitlines()):
            yield AdapterRow(
                body_text=line,
                body_text_source="plain",
                date_sent=f"2024-01-{i+1:02d}T00:00:00Z",
            )
""",
        encoding="utf-8",
    )

    db_path = tmp_path / "test.db"
    with connect(db_path, create=True) as conn:
        MigrationRunner(conn).apply_pending()

    registry = discover_adapters([tmp_path])
    adapter_cls = registry["ext_e2e"]
    adapter = adapter_cls()

    source = tmp_path / "data.txt"
    source.write_text("line one\nline two\nline three\n", encoding="utf-8")

    settings = Settings.load(db_path=db_path)
    with connect(db_path) as conn:
        report = adapter.run(source, conn, settings)

    assert report.rows_inserted == 3

    with connect(db_path) as conn:
        count = conn.execute("SELECT count(*) FROM chat_messages").fetchone()[0]
    assert count == 3
