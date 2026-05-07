"""CLI entrypoint for phdb."""

from __future__ import annotations

from pathlib import Path

import click

from phdb import __version__


@click.group()
@click.version_option(__version__, prog_name="phdb")
@click.option("--db", type=click.Path(), envvar="PHDB_DB_PATH", default=None, help="Path to SQLite database.")
@click.option("--instance-dir", type=click.Path(), envvar="PHDB_INSTANCE_DIR", default=None, help="Instance config directory.")
@click.option("--log-level", default="INFO", envvar="PHDB_LOG_LEVEL", help="Log level.")
@click.pass_context
def cli(ctx: click.Context, db: str | None, instance_dir: str | None, log_level: str) -> None:
    """phdb - Personal History Database."""
    from phdb.log import setup_logging
    from phdb.settings import Settings
    from phdb.validation import validate_instance

    ctx.ensure_object(dict)
    settings = Settings.load(
        db_path=db,
        instance_dir=instance_dir,
    )
    ctx.obj["settings"] = settings

    pii_literals: list[str] = list(
        settings.identity.owner_names
        | settings.identity.owner_emails
        | settings.identity.owner_phones
    )
    for handles in settings.identity.owner_handles.values():
        pii_literals.extend(handles)

    logger = setup_logging(level=log_level, pii_literals=pii_literals)

    for warning in validate_instance(settings):
        logger.warning(warning)

    if settings.instance_dir:
        atoms_path = Path(settings.instance_dir) / "atoms.toml"
        if atoms_path.is_file():
            from phdb.atoms.registry import AtomRegistry

            registry = AtomRegistry()
            registry.load_instance_types(atoms_path)
            ctx.obj["atom_registry"] = registry


@cli.command()
@click.option("--instance-migrations", type=click.Path(exists=True), default=None, help="Instance migrations directory.")
@click.pass_context
def migrate(ctx: click.Context, instance_migrations: str | None) -> None:
    """Apply pending database migrations."""
    from phdb.db import connect, ensure_vec_table
    from phdb.migrations.runner import MigrationRunner

    settings = ctx.obj["settings"]
    inst_mig_dir = Path(instance_migrations) if instance_migrations else None

    with connect(settings.db_path, load_vec=True) as conn:
        runner = MigrationRunner(conn, instance_dir=inst_mig_dir)
        applied = runner.apply_pending()
        if applied:
            click.echo(f"Applied {len(applied)} migration(s): {', '.join(applied)}")
        else:
            click.echo("Database is up to date.")

        ensure_vec_table(conn)

        click.echo("\nMigration status:")
        for mid, is_applied in runner.status():
            mark = "+" if is_applied else " "
            click.echo(f"  [{mark}] {mid}")


@cli.command()
@click.argument("source", type=click.Path(exists=True))
@click.option("--adapter", "-a", required=True, help="Adapter name to use.")
@click.option("--dry-run", is_flag=True, help="Parse and report without writing to DB.")
@click.pass_context
def ingest(ctx: click.Context, source: str, adapter: str, dry_run: bool) -> None:
    """Ingest a source file using the named adapter."""
    from phdb.adapters.loader import discover_adapters
    from phdb.db import connect
    from phdb.writelock import WriteLockError, write_lock

    settings = ctx.obj["settings"]
    builtin_dir = Path(__file__).parent / "adapters"
    search_paths = [builtin_dir, *settings.adapter_paths]
    adapters = discover_adapters(search_paths)

    if adapter not in adapters:
        available = ", ".join(sorted(adapters)) or "(none)"
        raise click.ClickException(f"Unknown adapter '{adapter}'. Available: {available}")

    adapter_cls = adapters[adapter]
    adapter_instance = adapter_cls()

    if dry_run:
        count = 0
        for _ in adapter_instance.iter_rows(Path(source)):
            count += 1
        click.echo(f"Dry run: {count} rows parsed by adapter '{adapter}'")
        return

    try:
        with write_lock(settings.db_path), connect(settings.db_path) as conn:
            report = adapter_instance.run(Path(source), conn, settings)
            click.echo(
                f"Ingested: {report.rows_inserted} inserted, "
                f"{report.rows_skipped} skipped, "
                f"{report.rows_yielded} total from '{adapter}'"
            )
    except WriteLockError as e:
        raise click.ClickException(str(e)) from e


@cli.command()
@click.pass_context
def stats(ctx: click.Context) -> None:
    """Show database statistics."""
    from phdb.db import connect

    settings = ctx.obj["settings"]

    with connect(settings.db_path, readonly=True) as conn:
        tables = [
            ("messages", None),
            ("source_files", None),
            ("documents", None),
            ("threads", None),
            ("recipients", None),
            ("attachments", None),
            ("bookmarks", None),
            ("connections", None),
        ]

        click.echo(f"Database: {settings.db_path}\n")
        click.echo("Row counts:")
        for table, _ in tables:
            try:
                count = conn.execute(f"SELECT count(*) FROM {table}").fetchone()[0]  # noqa: S608
                click.echo(f"  {table:25s} {count:>10,}")
            except Exception:
                click.echo(f"  {table:25s} (not found)")

        click.echo("\nSource breakdown:")
        try:
            rows = conn.execute(
                "SELECT source_kind, count(*), sum(message_count) FROM source_files GROUP BY source_kind ORDER BY count(*) DESC"
            ).fetchall()
            for kind, file_count, msg_count in rows:
                click.echo(f"  {kind or '(null)':25s} {file_count:>6} files  {msg_count or 0:>10,} messages")
        except Exception:
            click.echo("  (source_files table not found)")

        click.echo("\nEmbedding status:")
        try:
            total = conn.execute("SELECT count(*) FROM documents").fetchone()[0]
            embedded = conn.execute("SELECT count(*) FROM documents WHERE embedded_at IS NOT NULL").fetchone()[0]
            pct = (embedded / total * 100) if total else 0
            click.echo(f"  {embedded:,} / {total:,} documents embedded ({pct:.1f}%)")
        except Exception:
            click.echo("  (documents table not found)")


@cli.command()
@click.argument("text")
@click.option("--k", default=10, help="Number of results to return.")
@click.option("--mode", type=click.Choice(["hybrid", "fts", "semantic"]), default="hybrid")
@click.option("--since", default=None, help="Lower date bound (YYYY, YYYY-MM, YYYY-MM-DD).")
@click.option("--until", default=None, help="Upper date bound (YYYY, YYYY-MM, YYYY-MM-DD).")
@click.option("--include-bulk", is_flag=True, help="Include bulk/mailing-list messages.")
@click.option("--year-normalize/--no-year-normalize", default=True, help="Per-year IDF normalization.")
@click.pass_context
def query(
    ctx: click.Context,
    text: str,
    k: int,
    mode: str,
    since: str | None,
    until: str | None,
    include_bulk: bool,
    year_normalize: bool,
) -> None:
    """Search the personal history corpus."""
    from phdb.db import connect
    from phdb.embed_service import EmbedClient
    from phdb.query import search as query_search

    settings = ctx.obj["settings"]
    embed_client: EmbedClient | None = None
    if mode in ("hybrid", "semantic"):
        embed_client = EmbedClient.from_settings(settings)

    with connect(settings.db_path, load_vec=True, readonly=True) as conn:
        result = query_search(
            conn,
            text,
            embed_client=embed_client,
            k=k,
            mode=mode,
            since=since,
            until=until,
            include_bulk=include_bulk,
            year_normalize=year_normalize,
        )

    if "error" in result:
        raise click.ClickException(result["error"])

    click.echo(f"Query:  {text!r}")
    if since or until:
        click.echo(f"Range:  {since or '*'}  to  {until or '*'}")
    click.echo(f"Mode:   {result['mode']}  |  FTS: {result['fts_mode']}")
    click.echo(f"Sem:    {result['n_semantic']} candidates  |  FTS: {result['n_fts']} candidates")
    click.echo()

    rows = result.get("results", [])
    if not rows:
        click.echo("No matches.")
        return

    for i, r in enumerate(rows, 1):
        score = r.get("score", 0)
        date = r.get("date", "") or ""
        direction = r.get("direction", "") or ""
        sender = r.get("sender_address", "") or ""
        subject = r.get("subject", "(no subject)") or "(no subject)"
        snippet = r.get("snippet", "")

        click.echo(f"{i:2d}. [score={score:.4f}] {date}  {direction:8s}  {sender}")
        click.echo(f"    Subject: {subject[:90]}")
        click.echo(f"    Chunk #{r.get('chunk_index', 0)} of msg #{r.get('msg_id', '?')} "
                    f"(thread {r.get('thread_id', 'n/a')})")
        click.echo(f"    {snippet[:200]}...")
        click.echo()


@cli.command()
@click.option("--limit", type=int, default=None, help="Max messages to embed this run.")
@click.option("--batch-size", type=int, default=32, help="Chunks per Ollama batch call.")
@click.option("--status", "show_status", is_flag=True, help="Print embed status and exit.")
@click.option("--dry-run", is_flag=True, help="Compute chunks, report counts, don't write.")
@click.option("--force", is_flag=True, help="Break stale write lock if PID is dead.")
@click.pass_context
def embed(
    ctx: click.Context,
    limit: int | None,
    batch_size: int,
    show_status: bool,
    dry_run: bool,
    force: bool,
) -> None:
    """Embed pending message bodies into documents + doc_vectors."""
    from phdb.db import connect, ensure_vec_table
    from phdb.embed_pipeline import EmbedProgress, get_embed_status, run_embed_pipeline
    from phdb.embed_service import EmbedClient
    from phdb.writelock import WriteLockError, write_lock

    settings = ctx.obj["settings"]

    if show_status:
        with connect(settings.db_path, load_vec=True, readonly=True) as conn:
            st = get_embed_status(conn)
            click.echo("Embed status:")
            click.echo(f"  total non-bulk eligible:    {st.total_eligible:,}")
            click.echo(f"  done:                       {st.done:,}")
            click.echo(f"  pending:                    {st.pending:,}")
            click.echo(f"  chunks embedded:            {st.chunks_embedded:,}")
            click.echo(f"  vectors stored:             {st.vectors_stored:,}")
        return

    client = EmbedClient.from_settings(settings)

    reachable, models = client.health_check()
    if not reachable:
        raise click.ClickException(
            f"Ollama unreachable at {client.endpoint}. Start with: ollama serve"
        )
    click.echo(f"Ollama OK. Models: {models}")
    if not any(client.model in m for m in models):
        click.echo(f"WARNING: '{client.model}' not in Ollama. Run: ollama pull {client.model}")

    actual_dim = client.verify_dim()
    if actual_dim != client.dim:
        raise click.ClickException(
            f"Model returned {actual_dim}-dim vectors but schema expects {client.dim}."
        )

    def _progress(p: EmbedProgress) -> None:
        click.echo(
            f"  msgs {p.messages_done:,}/{p.messages_total:,} ({p.pct:.1f}%)"
            f"  chunks {p.chunks_done:,}  rate {p.chunk_rate:.1f}/s"
            f"  ETA {p.eta_min:.1f} min"
        )

    try:
        with write_lock(settings.db_path, force=force), connect(settings.db_path, load_vec=True) as conn:
            ensure_vec_table(conn)
            result = run_embed_pipeline(
                conn,
                client,
                batch_size=batch_size,
                limit=limit,
                dry_run=dry_run,
                progress_cb=_progress,
            )
    except WriteLockError as e:
        raise click.ClickException(str(e)) from e

    label = "DRY RUN" if dry_run else "DONE"
    rate = result.chunks_embedded / result.elapsed_s if result.elapsed_s else 0
    click.echo(
        f"\n{label}  messages={result.messages_processed:,}"
        f"  chunks={result.chunks_embedded:,}"
        f"  elapsed={result.elapsed_s / 60:.1f} min"
        f"  rate={rate:.1f} chunks/s"
    )
