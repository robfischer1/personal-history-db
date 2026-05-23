"""Tests for the Click CLI."""

from __future__ import annotations

from pathlib import Path

from click.testing import CliRunner

from phdb.cli import cli
from phdb.db import connect
from phdb.migrations.runner import MigrationRunner


def test_cli_help() -> None:
    runner = CliRunner()
    result = runner.invoke(cli, ["--help"])
    assert result.exit_code == 0
    assert "phdb" in result.output
    assert "migrate" in result.output
    assert "ingest" in result.output
    assert "stats" in result.output
    assert "query" in result.output
    assert "embed" in result.output


def test_cli_version() -> None:
    runner = CliRunner()
    result = runner.invoke(cli, ["--version"])
    assert result.exit_code == 0
    assert "0.3.0" in result.output


def test_cli_migrate(tmp_path: Path) -> None:
    db_path = tmp_path / "test.db"
    runner = CliRunner()
    result = runner.invoke(cli, ["--db", str(db_path), "migrate"])
    assert result.exit_code == 0
    assert "Applied 28 migration(s)" in result.output


def test_cli_migrate_idempotent(tmp_path: Path) -> None:
    db_path = tmp_path / "test.db"
    runner = CliRunner()
    runner.invoke(cli, ["--db", str(db_path), "migrate"])
    result = runner.invoke(cli, ["--db", str(db_path), "migrate"])
    assert result.exit_code == 0
    assert "up to date" in result.output


def test_cli_stats(tmp_path: Path) -> None:
    db_path = tmp_path / "test.db"
    with connect(db_path, create=True) as conn:
        MigrationRunner(conn).apply_pending()

    runner = CliRunner()
    result = runner.invoke(cli, ["--db", str(db_path), "stats"])
    assert result.exit_code == 0
    assert "messages" in result.output
    assert "Row counts" in result.output


def test_cli_query_fts(tmp_path: Path) -> None:
    """Query subcommand runs FTS search against a seeded DB."""
    from phdb.db import ensure_vec_table

    db_path = tmp_path / "test.db"
    with connect(db_path, create=True, load_vec=True) as conn:
        MigrationRunner(conn).apply_pending()
        ensure_vec_table(conn)
        conn.execute(
            "INSERT INTO source_files (id, source_path, source_org, file_kind, message_count)"
            " VALUES (1, '/test/inbox.mbox', 'gmail', 'mbox', 1)"
        )
        conn.execute(
            "INSERT INTO emails (id, schema_type, subject, sender_address, direction,"
            " date_sent, body_text, is_bulk, source_file_id)"
            " VALUES (1, 'EmailMessage', 'Test subject', 'alice@example.com', 'inbound',"
            " '2024-01-01T00:00:00Z', 'Test body content for query', 0, 1)"
        )
        conn.execute(
            "INSERT INTO chunks (id, schema_type, source_table, source_id,"
            " chunk_index, title, content)"
            " VALUES (1, 'EmailMessage', 'emails', 1, 0, 'Test subject',"
            " 'Test body content for query')"
        )
        conn.commit()

    runner = CliRunner()
    result = runner.invoke(cli, [
        "--db", str(db_path), "query", "Test body content", "--mode", "fts",
    ])
    assert result.exit_code == 0, result.output
    assert "alice@example.com" in result.output


def test_cli_embed_status(tmp_path: Path) -> None:
    db_path = tmp_path / "test.db"
    with connect(db_path, create=True, load_vec=True) as conn:
        MigrationRunner(conn).apply_pending()
        from phdb.db import ensure_vec_table

        ensure_vec_table(conn)

    runner = CliRunner()
    result = runner.invoke(cli, ["--db", str(db_path), "embed", "--status"])
    assert result.exit_code == 0
    assert "Embed status:" in result.output
    assert "pending" in result.output


def test_cli_embed_placeholder_gone(tmp_path: Path) -> None:
    db_path = tmp_path / "test.db"
    with connect(db_path, create=True, load_vec=True) as conn:
        MigrationRunner(conn).apply_pending()
        from phdb.db import ensure_vec_table

        ensure_vec_table(conn)

    runner = CliRunner()
    result = runner.invoke(cli, ["--db", str(db_path), "embed", "--status"])
    assert "not yet integrated" not in result.output


def test_cli_ingest_with_instance_dir(tmp_path: Path) -> None:
    """End-to-end: ingest with --instance-dir, verify direction inference."""
    db_path = tmp_path / "test.db"
    with connect(db_path, create=True) as conn:
        MigrationRunner(conn).apply_pending()

    # Create instance dir with identity config
    inst = tmp_path / "instance"
    inst.mkdir()
    (inst / "identity.toml").write_text(
        '[identity]\nowner_emails = ["sender@example.com"]\n',
        encoding="utf-8",
    )

    # Create a minimal mbox fixture
    mbox_path = tmp_path / "test.mbox"
    mbox_path.write_text(
        "From sender@example.com Mon Jan 15 10:00:00 2024\n"
        "Message-ID: <test-cli-001@example.com>\n"
        "From: sender@example.com\n"
        "To: friend@example.com\n"
        "Date: Mon, 15 Jan 2024 10:00:00 +0000\n"
        "Subject: Test\n"
        "\n"
        "Hello from the CLI test.\n"
        "\n",
        encoding="utf-8",
    )

    runner = CliRunner()
    result = runner.invoke(cli, [
        "--db", str(db_path),
        "--instance-dir", str(inst),
        "plugin", "ingest", "mbox", str(mbox_path),
    ])
    assert result.exit_code == 0, result.output
    assert "inserted=1" in result.output

    with connect(db_path) as conn:
        direction = conn.execute("SELECT direction FROM emails LIMIT 1").fetchone()[0]
    assert direction == "outbound"


def test_cli_init_fresh(tmp_path: Path) -> None:
    """Init creates a new instance directory with all template files."""
    target = tmp_path / "my-instance"
    runner = CliRunner()
    result = runner.invoke(cli, ["init", str(target)])
    assert result.exit_code == 0, result.output
    assert target.is_dir()
    assert (target / "paths.toml").is_file()
    assert (target / "identity.toml").is_file()
    assert (target / "embedding.toml").is_file()
    assert (target / "sources.toml").is_file()
    assert (target / "adapters").is_dir()
    assert "Instance directory ready" in result.output


def test_cli_init_sets_db_path(tmp_path: Path) -> None:
    """Init with --db-path fills in paths.toml."""
    target = tmp_path / "instance"
    db = tmp_path / "data" / "my.db"
    runner = CliRunner()
    result = runner.invoke(cli, ["init", str(target), "--db-path", str(db)])
    assert result.exit_code == 0, result.output
    content = (target / "paths.toml").read_text(encoding="utf-8")
    assert str(db).replace("\\", "/") in content


def test_cli_init_refuses_existing_config(tmp_path: Path) -> None:
    """Init refuses to overwrite an existing instance directory."""
    target = tmp_path / "instance"
    target.mkdir()
    (target / "paths.toml").write_text("existing = true\n", encoding="utf-8")
    runner = CliRunner()
    result = runner.invoke(cli, ["init", str(target)])
    assert result.exit_code != 0
    assert "already has config files" in result.output


def test_cli_init_then_migrate(tmp_path: Path) -> None:
    """Full flow: init -> set db_path -> migrate."""
    inst = tmp_path / "instance"
    db = tmp_path / "test.db"
    runner = CliRunner()
    runner.invoke(cli, ["init", str(inst), "--db-path", str(db)])
    result = runner.invoke(cli, ["--db", str(db), "--instance-dir", str(inst), "migrate"])
    assert result.exit_code == 0, result.output
    assert "Applied" in result.output
