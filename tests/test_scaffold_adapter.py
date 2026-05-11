"""Tests for scripts/scaffold_adapter.py."""

from __future__ import annotations

import textwrap
from pathlib import Path

import pytest

# Import the scaffolder module directly
import sys

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts"))
from scaffold_adapter import (
    create_fixture,
    generate_adapter,
    generate_test,
    scaffold,
    to_class_name,
)


class TestToClassName:
    def test_simple(self) -> None:
        assert to_class_name("goodreads") == "GoodreadsAdapter"

    def test_multi_word(self) -> None:
        assert to_class_name("call_log") == "CallLogAdapter"

    def test_triple_word(self) -> None:
        assert to_class_name("phone_sms_backup") == "PhoneSmsBackupAdapter"


class TestGenerateAdapter:
    def test_csv_adapter_has_class(self) -> None:
        src = generate_adapter("my_source", "my_platform", "csv", "Message")
        assert "class MySourceAdapter(Adapter):" in src

    def test_csv_adapter_has_iter_rows(self) -> None:
        src = generate_adapter("my_source", "my_platform", "csv", "Message")
        assert "def iter_rows(self, source_path: Path" in src

    def test_csv_adapter_imports_csv(self) -> None:
        src = generate_adapter("my_source", "my_platform", "csv", "Message")
        assert "import csv" in src

    def test_json_adapter_imports_json(self) -> None:
        src = generate_adapter("my_source", "my_platform", "json", "Message")
        assert "import json" in src

    def test_xml_adapter_imports_et(self) -> None:
        src = generate_adapter("my_source", "my_platform", "xml", "Message")
        assert "import xml.etree.ElementTree as ET" in src

    def test_mbox_adapter_imports_mailbox(self) -> None:
        src = generate_adapter("my_source", "my_platform", "mbox", "Message")
        assert "import mailbox" in src

    def test_sqlite_adapter_imports_sqlite3(self) -> None:
        src = generate_adapter("my_source", "my_platform", "sqlite", "Message")
        assert "import sqlite3" in src

    def test_schema_type_propagated(self) -> None:
        src = generate_adapter("my_source", "my_platform", "csv", "Book")
        assert 'schema_type = "Book"' in src

    def test_source_kind_propagated(self) -> None:
        src = generate_adapter("my_source", "my_platform", "csv", "Message")
        assert 'source_kind = "my_platform"' in src

    def test_name_propagated(self) -> None:
        src = generate_adapter("my_source", "my_platform", "csv", "Message")
        assert 'name = "my_source"' in src

    def test_dedup_strategy_mbox_uses_rfc822(self) -> None:
        src = generate_adapter("my_source", "my_platform", "mbox", "Message")
        assert "DedupStrategy.RFC822_MESSAGE_ID" in src

    def test_dedup_strategy_csv_uses_content_hash(self) -> None:
        src = generate_adapter("my_source", "my_platform", "csv", "Message")
        assert "DedupStrategy.CONTENT_HASH" in src

    def test_jsonl_adapter_has_line_parsing(self) -> None:
        src = generate_adapter("my_source", "my_platform", "jsonl", "Message")
        assert "json.loads(line)" in src


class TestGenerateTest:
    def test_has_test_class(self) -> None:
        src = generate_test("my_source", "Message", "sample.csv")
        assert "class TestMySourceAdapterIntegration:" in src

    def test_has_basic_ingest(self) -> None:
        src = generate_test("my_source", "Message", "sample.csv")
        assert "def test_basic_ingest" in src

    def test_has_idempotency(self) -> None:
        src = generate_test("my_source", "Message", "sample.csv")
        assert "def test_idempotent_rerun" in src

    def test_has_schema_type_check(self) -> None:
        src = generate_test("my_source", "Message", "sample.csv")
        assert 'assert all(t[0] == "Message"' in src

    def test_fixture_path_correct(self) -> None:
        src = generate_test("my_source", "Message", "data.csv")
        assert '"fixtures" / "my_source" / "data.csv"' in src

    def test_imports_correct_adapter(self) -> None:
        src = generate_test("my_source", "Message", "sample.csv")
        assert "from phdb.adapters.my_source import MySourceAdapter" in src

    def test_uses_conftest_fixtures(self) -> None:
        src = generate_test("my_source", "Message", "sample.csv")
        assert "migrated_db" in src
        assert "test_settings" in src


class TestCreateFixture:
    def test_csv_truncation(self, tmp_path: Path) -> None:
        sample = tmp_path / "data.csv"
        sample.write_text("a,b,c\n1,2,3\n4,5,6\n7,8,9\n10,11,12\n13,14,15\n16,17,18\n19,20,21\n")
        fixture_dir = tmp_path / "fixtures" / "test_adapter"
        filename = create_fixture("csv", sample, fixture_dir)
        result = (fixture_dir / filename).read_text()
        lines = [l for l in result.strip().split("\n") if l]
        assert lines[0] == "a,b,c"
        assert len(lines) <= 6  # header + 5 data rows

    def test_json_list_truncation(self, tmp_path: Path) -> None:
        import json

        sample = tmp_path / "data.json"
        sample.write_text(json.dumps([{"x": i} for i in range(20)]))
        fixture_dir = tmp_path / "fixtures" / "test_adapter"
        filename = create_fixture("json", sample, fixture_dir)
        data = json.loads((fixture_dir / filename).read_text())
        assert len(data) == 5

    def test_json_nested_truncation(self, tmp_path: Path) -> None:
        import json

        sample = tmp_path / "data.json"
        sample.write_text(json.dumps({"items": [{"x": i} for i in range(20)], "meta": "keep"}))
        fixture_dir = tmp_path / "fixtures" / "test_adapter"
        filename = create_fixture("json", sample, fixture_dir)
        data = json.loads((fixture_dir / filename).read_text())
        assert len(data["items"]) == 5
        assert data["meta"] == "keep"

    def test_jsonl_truncation(self, tmp_path: Path) -> None:
        import json

        sample = tmp_path / "data.jsonl"
        lines = [json.dumps({"x": i}) + "\n" for i in range(20)]
        sample.write_text("".join(lines))
        fixture_dir = tmp_path / "fixtures" / "test_adapter"
        filename = create_fixture("jsonl", sample, fixture_dir)
        result_lines = [l for l in (fixture_dir / filename).read_text().strip().split("\n") if l]
        assert len(result_lines) == 5

    def test_xml_truncation(self, tmp_path: Path) -> None:
        sample = tmp_path / "data.xml"
        sample.write_text(
            '<?xml version="1.0"?>\n<root>'
            + "".join(f"<item id='{i}'/>" for i in range(20))
            + "</root>"
        )
        fixture_dir = tmp_path / "fixtures" / "test_adapter"
        filename = create_fixture("xml", sample, fixture_dir)
        import xml.etree.ElementTree as ET

        tree = ET.parse(fixture_dir / filename)
        assert len(list(tree.getroot())) == 5

    def test_fallback_copy(self, tmp_path: Path) -> None:
        sample = tmp_path / "data.mbox"
        sample.write_bytes(b"From sender@example.com\nSubject: hi\n\nHello\n" * 100)
        fixture_dir = tmp_path / "fixtures" / "test_adapter"
        filename = create_fixture("mbox", sample, fixture_dir)
        result = (fixture_dir / filename).read_bytes()
        assert len(result) <= 8192

    def test_creates_directory(self, tmp_path: Path) -> None:
        sample = tmp_path / "data.csv"
        sample.write_text("a,b\n1,2\n")
        fixture_dir = tmp_path / "deep" / "nested" / "fixtures"
        create_fixture("csv", sample, fixture_dir)
        assert fixture_dir.exists()


class TestScaffold:
    def test_creates_three_files(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setattr("scaffold_adapter.ADAPTERS_DIR", tmp_path / "adapters")
        monkeypatch.setattr("scaffold_adapter.TESTS_DIR", tmp_path / "tests")
        monkeypatch.setattr("scaffold_adapter.FIXTURES_DIR", tmp_path / "tests" / "fixtures")
        monkeypatch.setattr("scaffold_adapter.PROJECT_ROOT", tmp_path)
        (tmp_path / "adapters").mkdir()
        (tmp_path / "tests").mkdir()

        created = scaffold("test_src", "platform", "csv", "Message")
        assert (tmp_path / "adapters" / "test_src.py").exists()
        assert (tmp_path / "tests" / "test_test_src_adapter.py").exists()
        assert (tmp_path / "tests" / "fixtures" / "test_src").is_dir()

    def test_with_sample_file(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setattr("scaffold_adapter.ADAPTERS_DIR", tmp_path / "adapters")
        monkeypatch.setattr("scaffold_adapter.TESTS_DIR", tmp_path / "tests")
        monkeypatch.setattr("scaffold_adapter.FIXTURES_DIR", tmp_path / "tests" / "fixtures")
        monkeypatch.setattr("scaffold_adapter.PROJECT_ROOT", tmp_path)
        (tmp_path / "adapters").mkdir()
        (tmp_path / "tests").mkdir()

        sample = tmp_path / "data.csv"
        sample.write_text("x,y\n1,2\n3,4\n")

        created = scaffold("test_src", "platform", "csv", "Message", sample_path=sample)
        assert "fixture" in created
        assert created["fixture"].exists()

    def test_rejects_bad_name(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setattr("scaffold_adapter.ADAPTERS_DIR", tmp_path / "adapters")
        with pytest.raises(ValueError, match="snake_case"):
            scaffold("BadName", "platform", "csv", "Message")

    def test_rejects_bad_file_kind(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setattr("scaffold_adapter.ADAPTERS_DIR", tmp_path / "adapters")
        with pytest.raises(ValueError, match="file_kind"):
            scaffold("test_src", "platform", "docx", "Message")

    def test_rejects_existing_adapter(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setattr("scaffold_adapter.ADAPTERS_DIR", tmp_path / "adapters")
        monkeypatch.setattr("scaffold_adapter.TESTS_DIR", tmp_path / "tests")
        (tmp_path / "adapters").mkdir()
        (tmp_path / "adapters" / "test_src.py").write_text("existing")
        with pytest.raises(FileExistsError):
            scaffold("test_src", "platform", "csv", "Message")

    def test_dry_run_creates_nothing(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setattr("scaffold_adapter.ADAPTERS_DIR", tmp_path / "adapters")
        monkeypatch.setattr("scaffold_adapter.TESTS_DIR", tmp_path / "tests")
        monkeypatch.setattr("scaffold_adapter.FIXTURES_DIR", tmp_path / "tests" / "fixtures")
        monkeypatch.setattr("scaffold_adapter.PROJECT_ROOT", tmp_path)
        (tmp_path / "adapters").mkdir()
        (tmp_path / "tests").mkdir()

        created = scaffold("test_src", "platform", "csv", "Message", dry_run=True)
        assert len(created) == 0
        assert not (tmp_path / "adapters" / "test_src.py").exists()

    def test_generated_adapter_is_valid_python(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setattr("scaffold_adapter.ADAPTERS_DIR", tmp_path / "adapters")
        monkeypatch.setattr("scaffold_adapter.TESTS_DIR", tmp_path / "tests")
        monkeypatch.setattr("scaffold_adapter.FIXTURES_DIR", tmp_path / "tests" / "fixtures")
        monkeypatch.setattr("scaffold_adapter.PROJECT_ROOT", tmp_path)
        (tmp_path / "adapters").mkdir()
        (tmp_path / "tests").mkdir()

        for kind in ("csv", "json", "xml", "mbox", "sqlite", "jsonl", "tsv"):
            adapter_path = tmp_path / "adapters" / f"test_{kind}.py"
            if adapter_path.exists():
                adapter_path.unlink()
            test_path = tmp_path / "tests" / f"test_test_{kind}_adapter.py"
            if test_path.exists():
                test_path.unlink()
            scaffold(f"test_{kind}", "platform", kind, "Message")
            source = adapter_path.read_text()
            compile(source, str(adapter_path), "exec")  # SyntaxError if invalid

    def test_generated_test_is_valid_python(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setattr("scaffold_adapter.ADAPTERS_DIR", tmp_path / "adapters")
        monkeypatch.setattr("scaffold_adapter.TESTS_DIR", tmp_path / "tests")
        monkeypatch.setattr("scaffold_adapter.FIXTURES_DIR", tmp_path / "tests" / "fixtures")
        monkeypatch.setattr("scaffold_adapter.PROJECT_ROOT", tmp_path)
        (tmp_path / "adapters").mkdir()
        (tmp_path / "tests").mkdir()

        scaffold("test_src", "platform", "csv", "Message")
        test_path = tmp_path / "tests" / "test_test_src_adapter.py"
        source = test_path.read_text()
        compile(source, str(test_path), "exec")
