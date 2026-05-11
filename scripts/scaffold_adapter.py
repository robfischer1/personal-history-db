#!/usr/bin/env python3
"""Scaffold a new personal-history-db adapter.

Generates three files from CLI args:
  - src/phdb/adapters/<name>.py        (adapter class with iter_rows stub)
  - tests/test_<name>_adapter.py       (integration tests against fixture)
  - tests/fixtures/<name>/<sample>     (truncated copy of the sample file)
"""

from __future__ import annotations

import argparse
import csv
import io
import json
import re
import shutil
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
ADAPTERS_DIR = PROJECT_ROOT / "src" / "phdb" / "adapters"
TESTS_DIR = PROJECT_ROOT / "tests"
FIXTURES_DIR = TESTS_DIR / "fixtures"

VALID_FILE_KINDS = ("csv", "json", "xml", "mbox", "sqlite", "jsonl", "tsv")
VALID_NAME_RE = re.compile(r"^[a-z][a-z0-9_]*$")

# --- Templates ---------------------------------------------------------------

_ADAPTER_HEADER = '''\
"""{title} adapter — ingests {source_kind} {file_kind} exports."""

from __future__ import annotations

import hashlib
from collections.abc import Iterator
from pathlib import Path

from phdb.adapters.base import Adapter, AdapterRow, DedupStrategy
from phdb.log import get_logger
'''

_ITER_ROWS_STUBS: dict[str, str] = {
    "csv": '''\
{extra_imports}
log = get_logger("phdb.adapters.{name}")


class {class_name}(Adapter):
    """{docstring}"""

    name = "{name}"
    source_kind = "{source_kind}"
    file_kind = "csv"
    schema_type = "{schema_type}"
    dedup_strategy = DedupStrategy.CONTENT_HASH
    batch_size = 500

    def iter_rows(self, source_path: Path, **kwargs: object) -> Iterator[AdapterRow]:
        with open(source_path, encoding="utf-8-sig") as f:
            rdr = csv.DictReader(f)
            for row in rdr:
                # TODO: extract fields from row dict
                body = ""  # TODO: build body text
                dedup_seed = f"{name}|{{body}}"
                raw_hash = hashlib.sha256(dedup_seed.encode()).hexdigest()

                yield AdapterRow(
                    schema_type=self.schema_type,
                    rfc822_message_id=f"{name}:{{raw_hash}}",
                    subject=None,  # TODO
                    body_text=body,
                    body_text_source="{name}-csv",
                    raw_hash=raw_hash,
                    body_text_hash=hashlib.sha256(body.encode()).hexdigest(),
                    thread_key="{name}:all",
                )
''',
    "tsv": '''\
{extra_imports}
log = get_logger("phdb.adapters.{name}")


class {class_name}(Adapter):
    """{docstring}"""

    name = "{name}"
    source_kind = "{source_kind}"
    file_kind = "tsv"
    schema_type = "{schema_type}"
    dedup_strategy = DedupStrategy.CONTENT_HASH
    batch_size = 500

    def iter_rows(self, source_path: Path, **kwargs: object) -> Iterator[AdapterRow]:
        with open(source_path, encoding="utf-8-sig") as f:
            rdr = csv.DictReader(f, delimiter="\\t")
            for row in rdr:
                # TODO: extract fields from row dict
                body = ""  # TODO: build body text
                dedup_seed = f"{name}|{{body}}"
                raw_hash = hashlib.sha256(dedup_seed.encode()).hexdigest()

                yield AdapterRow(
                    schema_type=self.schema_type,
                    rfc822_message_id=f"{name}:{{raw_hash}}",
                    subject=None,  # TODO
                    body_text=body,
                    body_text_source="{name}-tsv",
                    raw_hash=raw_hash,
                    body_text_hash=hashlib.sha256(body.encode()).hexdigest(),
                    thread_key="{name}:all",
                )
''',
    "json": '''\
{extra_imports}
log = get_logger("phdb.adapters.{name}")


class {class_name}(Adapter):
    """{docstring}"""

    name = "{name}"
    source_kind = "{source_kind}"
    file_kind = "json"
    schema_type = "{schema_type}"
    dedup_strategy = DedupStrategy.CONTENT_HASH
    batch_size = 500

    def iter_rows(self, source_path: Path, **kwargs: object) -> Iterator[AdapterRow]:
        with open(source_path, encoding="utf-8") as f:
            data = json.load(f)

        items = data  # TODO: adjust path into JSON structure (e.g. data["items"])
        for item in items:
            # TODO: extract fields from item dict
            body = ""  # TODO
            dedup_seed = f"{name}|{{body}}"
            raw_hash = hashlib.sha256(dedup_seed.encode()).hexdigest()

            yield AdapterRow(
                schema_type=self.schema_type,
                rfc822_message_id=f"{name}:{{raw_hash}}",
                subject=None,  # TODO
                body_text=body,
                body_text_source="{name}-json",
                raw_hash=raw_hash,
                body_text_hash=hashlib.sha256(body.encode()).hexdigest(),
                thread_key="{name}:all",
            )
''',
    "jsonl": '''\
{extra_imports}
log = get_logger("phdb.adapters.{name}")


class {class_name}(Adapter):
    """{docstring}"""

    name = "{name}"
    source_kind = "{source_kind}"
    file_kind = "jsonl"
    schema_type = "{schema_type}"
    dedup_strategy = DedupStrategy.CONTENT_HASH
    batch_size = 500

    def iter_rows(self, source_path: Path, **kwargs: object) -> Iterator[AdapterRow]:
        with open(source_path, encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                item = json.loads(line)

                # TODO: extract fields from item dict
                body = ""  # TODO
                dedup_seed = f"{name}|{{body}}"
                raw_hash = hashlib.sha256(dedup_seed.encode()).hexdigest()

                yield AdapterRow(
                    schema_type=self.schema_type,
                    rfc822_message_id=f"{name}:{{raw_hash}}",
                    subject=None,  # TODO
                    body_text=body,
                    body_text_source="{name}-jsonl",
                    raw_hash=raw_hash,
                    body_text_hash=hashlib.sha256(body.encode()).hexdigest(),
                    thread_key="{name}:all",
                )
''',
    "xml": '''\
{extra_imports}
log = get_logger("phdb.adapters.{name}")


class {class_name}(Adapter):
    """{docstring}"""

    name = "{name}"
    source_kind = "{source_kind}"
    file_kind = "xml"
    schema_type = "{schema_type}"
    dedup_strategy = DedupStrategy.CONTENT_HASH
    batch_size = 500

    def iter_rows(self, source_path: Path, **kwargs: object) -> Iterator[AdapterRow]:
        tree = ET.parse(source_path)  # noqa: S314
        root = tree.getroot()

        for elem in root:  # TODO: adjust element selector
            # TODO: extract fields from elem
            body = ""  # TODO
            dedup_seed = f"{name}|{{body}}"
            raw_hash = hashlib.sha256(dedup_seed.encode()).hexdigest()

            yield AdapterRow(
                schema_type=self.schema_type,
                rfc822_message_id=f"{name}:{{raw_hash}}",
                subject=None,  # TODO
                body_text=body,
                body_text_source="{name}-xml",
                raw_hash=raw_hash,
                body_text_hash=hashlib.sha256(body.encode()).hexdigest(),
                thread_key="{name}:all",
            )
''',
    "mbox": '''\
{extra_imports}
log = get_logger("phdb.adapters.{name}")


class {class_name}(Adapter):
    """{docstring}"""

    name = "{name}"
    source_kind = "{source_kind}"
    file_kind = "mbox"
    schema_type = "{schema_type}"
    dedup_strategy = DedupStrategy.RFC822_MESSAGE_ID
    batch_size = 500

    def iter_rows(self, source_path: Path, **kwargs: object) -> Iterator[AdapterRow]:
        mbox = mailbox.mbox(str(source_path))
        for msg in mbox:
            msg_id = msg.get("Message-ID", "")
            subject = msg.get("Subject", "")
            sender = msg.get("From", "")
            date_str = msg.get("Date", "")

            body = ""
            if msg.is_multipart():
                for part in msg.walk():
                    if part.get_content_type() == "text/plain":
                        payload = part.get_payload(decode=True)
                        if payload:
                            body = payload.decode("utf-8", errors="replace")
                            break
            else:
                payload = msg.get_payload(decode=True)
                if payload:
                    body = payload.decode("utf-8", errors="replace")

            yield AdapterRow(
                schema_type=self.schema_type,
                rfc822_message_id=msg_id,
                subject=subject,
                sender_address=sender,
                date_sent=self.parse_date(date_str) if date_str else None,
                body_text=body,
                body_text_source="{name}-mbox",
                thread_key="{name}:all",
            )
''',
    "sqlite": '''\
{extra_imports}
log = get_logger("phdb.adapters.{name}")


class {class_name}(Adapter):
    """{docstring}"""

    name = "{name}"
    source_kind = "{source_kind}"
    file_kind = "sqlite"
    schema_type = "{schema_type}"
    dedup_strategy = DedupStrategy.CONTENT_HASH
    batch_size = 500

    def iter_rows(self, source_path: Path, **kwargs: object) -> Iterator[AdapterRow]:
        src_conn = sqlite3.connect(source_path)
        src_conn.row_factory = sqlite3.Row
        try:
            for row in src_conn.execute("SELECT * FROM table_name"):  # TODO: adjust query
                # TODO: extract fields from row
                body = ""  # TODO
                dedup_seed = f"{name}|{{body}}"
                raw_hash = hashlib.sha256(dedup_seed.encode()).hexdigest()

                yield AdapterRow(
                    schema_type=self.schema_type,
                    rfc822_message_id=f"{name}:{{raw_hash}}",
                    subject=None,  # TODO
                    body_text=body,
                    body_text_source="{name}-sqlite",
                    raw_hash=raw_hash,
                    body_text_hash=hashlib.sha256(body.encode()).hexdigest(),
                    thread_key="{name}:all",
                )
        finally:
            src_conn.close()
''',
}

_EXTRA_IMPORTS: dict[str, str] = {
    "csv": "import csv",
    "tsv": "import csv",
    "json": "import json",
    "jsonl": "import json",
    "xml": "import xml.etree.ElementTree as ET",
    "mbox": "import mailbox",
    "sqlite": "import sqlite3",
}

_TEST_TEMPLATE = '''\
"""Tests for the {name} adapter."""

from __future__ import annotations

from pathlib import Path

from phdb.adapters.{name} import {class_name}
from phdb.db import connect

FIXTURE = Path(__file__).parent / "fixtures" / "{name}" / "{fixture_filename}"


class Test{class_name}Integration:
    def test_basic_ingest(self, migrated_db: Path, test_settings) -> None:
        test_settings.db_path = migrated_db
        adapter = {class_name}()
        with connect(migrated_db) as conn:
            report = adapter.run(FIXTURE, conn, test_settings)
        assert report.rows_inserted > 0  # TODO: set exact expected count
        assert report.rows_skipped == 0

    def test_schema_type(self, migrated_db: Path, test_settings) -> None:
        test_settings.db_path = migrated_db
        adapter = {class_name}()
        with connect(migrated_db) as conn:
            adapter.run(FIXTURE, conn, test_settings)
            types = conn.execute("SELECT DISTINCT schema_type FROM messages").fetchall()
        assert all(t[0] == "{schema_type}" for t in types)

    def test_idempotent_rerun(self, migrated_db: Path, test_settings) -> None:
        test_settings.db_path = migrated_db
        adapter = {class_name}()
        with connect(migrated_db) as conn:
            adapter.run(FIXTURE, conn, test_settings)
        adapter2 = {class_name}()
        with connect(migrated_db) as conn:
            r2 = adapter2.run(FIXTURE, conn, test_settings)
        assert r2.rows_inserted == 0
        assert r2.rows_skipped == r2.rows_yielded

    def test_thread_created(self, migrated_db: Path, test_settings) -> None:
        test_settings.db_path = migrated_db
        adapter = {class_name}()
        with connect(migrated_db) as conn:
            report = adapter.run(FIXTURE, conn, test_settings)
        assert report.threads_created >= 1

    def test_message_thread_bridge(self, migrated_db: Path, test_settings) -> None:
        test_settings.db_path = migrated_db
        adapter = {class_name}()
        with connect(migrated_db) as conn:
            report = adapter.run(FIXTURE, conn, test_settings)
            bridge = conn.execute("SELECT COUNT(*) FROM message_threads").fetchone()[0]
        assert bridge == report.rows_inserted
'''


# --- Fixture truncation -------------------------------------------------------

def _truncate_csv(src: Path, dst: Path, max_rows: int = 5) -> str:
    with open(src, encoding="utf-8-sig") as f:
        reader = csv.reader(f)
        rows = []
        for i, row in enumerate(reader):
            rows.append(row)
            if i >= max_rows:  # header + max_rows data rows
                break
    buf = io.StringIO()
    writer = csv.writer(buf)
    writer.writerows(rows)
    dst.write_text(buf.getvalue(), encoding="utf-8")
    return dst.name


def _truncate_tsv(src: Path, dst: Path, max_rows: int = 5) -> str:
    with open(src, encoding="utf-8-sig") as f:
        lines = []
        for i, line in enumerate(f):
            lines.append(line)
            if i >= max_rows:
                break
    dst.write_text("".join(lines), encoding="utf-8")
    return dst.name


def _truncate_json(src: Path, dst: Path, max_items: int = 5) -> str:
    with open(src, encoding="utf-8") as f:
        data = json.load(f)
    if isinstance(data, list):
        data = data[:max_items]
    elif isinstance(data, dict):
        for key, val in data.items():
            if isinstance(val, list):
                data[key] = val[:max_items]
                break
    dst.write_text(json.dumps(data, indent=2, ensure_ascii=False), encoding="utf-8")
    return dst.name


def _truncate_jsonl(src: Path, dst: Path, max_lines: int = 5) -> str:
    lines = []
    with open(src, encoding="utf-8") as f:
        for line in f:
            if line.strip():
                lines.append(line)
                if len(lines) >= max_lines:
                    break
    dst.write_text("".join(lines), encoding="utf-8")
    return dst.name


def _truncate_xml(src: Path, dst: Path, max_elements: int = 5) -> str:
    import xml.etree.ElementTree as ET

    tree = ET.parse(src)  # noqa: S314
    root = tree.getroot()
    children = list(root)
    for child in children[max_elements:]:
        root.remove(child)
    tree.write(str(dst), encoding="unicode", xml_declaration=True)
    return dst.name


def _copy_raw(src: Path, dst: Path, max_bytes: int = 8192) -> str:
    """Fallback: copy up to max_bytes from the source."""
    with open(src, "rb") as f:
        chunk = f.read(max_bytes)
    dst.write_bytes(chunk)
    return dst.name


_TRUNCATORS = {
    "csv": _truncate_csv,
    "tsv": _truncate_tsv,
    "json": _truncate_json,
    "jsonl": _truncate_jsonl,
    "xml": _truncate_xml,
}


def create_fixture(file_kind: str, sample_path: Path, fixture_dir: Path) -> str:
    """Create a truncated fixture from a sample file. Returns the fixture filename."""
    fixture_dir.mkdir(parents=True, exist_ok=True)
    dst = fixture_dir / sample_path.name
    truncator = _TRUNCATORS.get(file_kind)
    if truncator:
        return truncator(sample_path, dst)
    if file_kind == "sqlite":
        shutil.copy2(sample_path, dst)
        return dst.name
    return _copy_raw(sample_path, dst)


# --- Code generation ----------------------------------------------------------

def to_class_name(name: str) -> str:
    """Convert snake_case adapter name to PascalCase class name."""
    return "".join(word.capitalize() for word in name.split("_")) + "Adapter"


def generate_adapter(
    name: str,
    source_kind: str,
    file_kind: str,
    schema_type: str,
) -> str:
    """Generate the adapter .py source code."""
    class_name = to_class_name(name)
    title = name.replace("_", " ").title()
    extra_imports = _EXTRA_IMPORTS.get(file_kind, "")

    header = _ADAPTER_HEADER.format(
        title=title,
        source_kind=source_kind,
        file_kind=file_kind,
    )

    stub_template = _ITER_ROWS_STUBS.get(file_kind)
    if not stub_template:
        stub_template = _ITER_ROWS_STUBS["json"]

    body = stub_template.format(
        name=name,
        class_name=class_name,
        source_kind=source_kind,
        schema_type=schema_type,
        docstring=f"Ingest {source_kind} {file_kind} exports.",
        extra_imports=extra_imports,
    )

    return header + body


def generate_test(
    name: str,
    schema_type: str,
    fixture_filename: str,
) -> str:
    """Generate the test .py source code."""
    class_name = to_class_name(name)
    return _TEST_TEMPLATE.format(
        name=name,
        class_name=class_name,
        schema_type=schema_type,
        fixture_filename=fixture_filename,
    )


# --- Main ---------------------------------------------------------------------

def scaffold(
    name: str,
    source_kind: str,
    file_kind: str,
    schema_type: str,
    sample_path: Path | None = None,
    dry_run: bool = False,
) -> dict[str, Path]:
    """Run the full scaffold. Returns dict of label -> created path."""
    if not VALID_NAME_RE.match(name):
        raise ValueError(f"Adapter name must be lowercase snake_case: {name!r}")
    if file_kind not in VALID_FILE_KINDS:
        raise ValueError(f"file_kind must be one of {VALID_FILE_KINDS}, got {file_kind!r}")

    adapter_path = ADAPTERS_DIR / f"{name}.py"
    test_path = TESTS_DIR / f"test_{name}_adapter.py"
    fixture_dir = FIXTURES_DIR / name

    if adapter_path.exists():
        raise FileExistsError(f"Adapter already exists: {adapter_path}")
    if test_path.exists():
        raise FileExistsError(f"Test file already exists: {test_path}")

    fixture_filename = "sample.dat"
    if sample_path:
        fixture_filename = sample_path.name

    adapter_src = generate_adapter(name, source_kind, file_kind, schema_type)
    test_src = generate_test(name, schema_type, fixture_filename)

    created: dict[str, Path] = {}

    if dry_run:
        print(f"[dry-run] Would create: {adapter_path}")
        print(f"[dry-run] Would create: {test_path}")
        print(f"[dry-run] Would create: {fixture_dir}/")
        if sample_path:
            print(f"[dry-run] Would truncate {sample_path} -> {fixture_dir / sample_path.name}")
        return created

    adapter_path.write_text(adapter_src, encoding="utf-8")
    created["adapter"] = adapter_path
    print(f"  Created {adapter_path.relative_to(PROJECT_ROOT)}")

    test_path.write_text(test_src, encoding="utf-8")
    created["test"] = test_path
    print(f"  Created {test_path.relative_to(PROJECT_ROOT)}")

    fixture_dir.mkdir(parents=True, exist_ok=True)
    created["fixture_dir"] = fixture_dir
    if sample_path and sample_path.exists():
        fixture_filename = create_fixture(file_kind, sample_path, fixture_dir)
        created["fixture"] = fixture_dir / fixture_filename
        print(f"  Created {(fixture_dir / fixture_filename).relative_to(PROJECT_ROOT)}")
    else:
        print(f"  Created {fixture_dir.relative_to(PROJECT_ROOT)}/ (empty — add fixture manually)")

    return created


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Scaffold a new personal-history-db adapter.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""\
Examples:
  python scripts/scaffold_adapter.py --name call_log --source-kind android --file-kind xml --schema-type PhoneCall --sample path/to/calls.xml
  python scripts/scaffold_adapter.py --name notion --source-kind notion --file-kind json --schema-type Message
  python scripts/scaffold_adapter.py --name call_log --source-kind android --file-kind xml --schema-type PhoneCall --dry-run
""",
    )
    parser.add_argument("--name", required=True, help="Adapter name (lowercase snake_case)")
    parser.add_argument("--source-kind", required=True, help="Source platform (e.g. android, notion, goodreads)")
    parser.add_argument("--file-kind", required=True, choices=VALID_FILE_KINDS, help="Source file format")
    parser.add_argument("--schema-type", default="Message", help="Schema.org @type for rows (default: Message)")
    parser.add_argument("--sample", type=Path, help="Path to a sample source file (truncated copy goes to fixtures)")
    parser.add_argument("--dry-run", action="store_true", help="Show what would be created without writing files")
    args = parser.parse_args()

    print(f"Scaffolding adapter: {args.name}")
    try:
        scaffold(
            name=args.name,
            source_kind=args.source_kind,
            file_kind=args.file_kind,
            schema_type=args.schema_type,
            sample_path=args.sample,
            dry_run=args.dry_run,
        )
    except (ValueError, FileExistsError) as e:
        print(f"Error: {e}", file=sys.stderr)
        sys.exit(1)
    print("Done. Fill in the TODO markers in the generated files.")


if __name__ == "__main__":
    main()
