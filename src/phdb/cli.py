"""CLI entrypoint for phdb."""

from __future__ import annotations

import sys
from pathlib import Path
from typing import TYPE_CHECKING, Any

import click

if TYPE_CHECKING:
    import sqlite3

from phdb import __version__

if sys.stdout and hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
if sys.stderr and hasattr(sys.stderr, "reconfigure"):
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")


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

    logger = setup_logging(level=log_level, pii_literals=settings.identity.pii_literals())

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
@click.argument("directory", type=click.Path())
@click.option("--db-path", default=None, help="Override db_path in generated paths.toml.")
@click.pass_context
def init(ctx: click.Context, directory: str, db_path: str | None) -> None:
    """Scaffold a new instance directory with template config files.

    Creates DIRECTORY (if it doesn't exist) and populates it with starter
    TOML files. Edit them to match your setup, then run `phdb migrate`.
    """
    import shutil

    target = Path(directory).resolve()
    templates_dir = Path(__file__).parent / "templates"

    if target.exists() and any(target.iterdir()):
        existing = [f.name for f in target.iterdir() if f.suffix == ".toml"]
        if existing:
            raise click.ClickException(
                f"Directory already has config files: {', '.join(existing)}. "
                f"Use an empty directory or remove existing files."
            )

    target.mkdir(parents=True, exist_ok=True)

    for template in sorted(templates_dir.glob("*.toml")):
        dest = target / template.name
        if not dest.exists():
            shutil.copy2(template, dest)
            click.echo(f"  Created {dest}")

    (target / "adapters").mkdir(exist_ok=True)

    def _toml_path(p: Path | str) -> str:
        return str(p).replace("\\", "/")

    if db_path:
        resolved_db = _toml_path(db_path)
        resolved_data = _toml_path(Path(db_path).parent)
    else:
        default_db = target.parent / "data" / "personal-history.db"
        resolved_db = _toml_path(default_db)
        resolved_data = _toml_path(default_db.parent)

    paths_file = target / "paths.toml"
    content = paths_file.read_text(encoding="utf-8")
    content = content.replace('db_path = ""', f'db_path = "{resolved_db}"')
    content = content.replace('data_dir = ""', f'data_dir = "{resolved_data}"')
    paths_file.write_text(content, encoding="utf-8")

    click.echo(f"\nInstance directory ready at: {target}")
    click.echo("Next steps:")
    click.echo(f"  1. Edit {target / 'identity.toml'} with your details")
    click.echo(f"  2. Edit {target / 'paths.toml'} if you want a different DB location")
    click.echo(f"  3. Run: phdb --instance-dir {target} migrate")


@cli.command()
@click.option("--instance-migrations", type=click.Path(exists=True), default=None, help="Instance migrations directory.")
@click.pass_context
def migrate(ctx: click.Context, instance_migrations: str | None) -> None:
    """Apply pending database migrations."""
    from phdb.db import connect, ensure_vec_table
    from phdb.migrations.runner import MigrationRunner

    settings = ctx.obj["settings"]
    inst_mig_dir = Path(instance_migrations) if instance_migrations else None

    with connect(settings.db_path, load_vec=True, create=True) as conn:
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
@click.pass_context
def stats(ctx: click.Context) -> None:
    """Show database statistics."""
    from phdb.db import connect

    settings = ctx.obj["settings"]

    with connect(settings.db_path, readonly=True) as conn:
        tables = [
            ("observations", None),
            ("chat_messages", None),
            ("emails", None),
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
            total = conn.execute("SELECT count(*) FROM chunks").fetchone()[0]
            embedded = conn.execute("SELECT count(*) FROM chunks WHERE embedded_at IS NOT NULL").fetchone()[0]
            pct = (embedded / total * 100) if total else 0
            click.echo(f"  {embedded:,} / {total:,} chunks embedded ({pct:.1f}%)")
        except Exception:
            click.echo("  (chunks table not found)")


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
        decay = r.get("decay_score", 1.0)
        date = r.get("date", "") or ""
        direction = r.get("direction", "") or ""
        sender = r.get("sender_address", "") or ""
        subject = r.get("subject", "(no subject)") or "(no subject)"
        snippet = r.get("snippet", "")

        click.echo(f"{i:2d}. [score={score:.4f} decay={decay:.3f}] {date}  {direction:8s}  {sender}")
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


@cli.group()
def decay() -> None:
    """Decay scoring commands."""


@decay.command()
@click.option("--config", type=click.Path(exists=True), default=None, help="Path to decay_policy.toml.")
@click.pass_context
def populate(ctx: click.Context, config: str | None) -> None:
    """Compute initial scores for all unscored chunks."""
    from pathlib import Path as P

    from phdb.db import connect
    from phdb.scoring import DecayConfig, populate_initial_scores

    settings = ctx.obj["settings"]
    cfg = DecayConfig.load(P(config)) if config else DecayConfig.load()

    with connect(settings.db_path) as conn:
        count = populate_initial_scores(conn, cfg)
        click.echo(f"Populated {count:,} chunk scores.")


@decay.command()
@click.option("--config", type=click.Path(exists=True), default=None, help="Path to decay_policy.toml.")
@click.option("--tier", default=None, help="Recompute only this tier.")
@click.pass_context
def recompute(ctx: click.Context, config: str | None, tier: str | None) -> None:
    """Recompute scores for all chunks (reads engagement history)."""
    from pathlib import Path as P

    from phdb.db import connect
    from phdb.scoring import DecayConfig, batch_recompute

    settings = ctx.obj["settings"]
    cfg = DecayConfig.load(P(config)) if config else DecayConfig.load()

    with connect(settings.db_path) as conn:
        count = batch_recompute(conn, cfg, tier_filter=tier)
        click.echo(f"Recomputed {count:,} chunk scores.")


@decay.command(name="stats")
@click.pass_context
def decay_stats(ctx: click.Context) -> None:
    """Show decay score distribution by tier."""
    from phdb.db import connect

    settings = ctx.obj["settings"]

    with connect(settings.db_path, readonly=True) as conn:
        try:
            rows = conn.execute(
                "SELECT tier, count(*), avg(score), min(score), max(score)"
                " FROM chunk_scores GROUP BY tier ORDER BY avg(score) DESC"
            ).fetchall()
        except Exception:
            click.echo("chunk_scores table not found. Run: phdb migrate && phdb decay populate")
            return

        if not rows:
            click.echo("No scores computed yet. Run: phdb decay populate")
            return

        total = sum(r[1] for r in rows)
        click.echo(f"Decay scores: {total:,} chunks\n")
        click.echo(f"  {'Tier':<18s} {'Count':>8s} {'Avg':>8s} {'Min':>8s} {'Max':>8s}")
        click.echo(f"  {'-' * 18} {'-' * 8} {'-' * 8} {'-' * 8} {'-' * 8}")
        for tier, count, avg, mn, mx in rows:
            click.echo(f"  {tier:<18s} {count:>8,} {avg:>8.4f} {mn:>8.4f} {mx:>8.4f}")

        eng_count = conn.execute("SELECT count(*) FROM engagements").fetchone()[0]
        click.echo(f"\n  Engagement events: {eng_count:,}")

        unscored = conn.execute(
            "SELECT count(*) FROM chunks c"
            " LEFT JOIN chunk_scores cs ON cs.chunk_id = c.id"
            " WHERE cs.chunk_id IS NULL"
        ).fetchone()[0]
        if unscored:
            click.echo(f"  Unscored chunks: {unscored:,} (run: phdb decay populate)")

        last_recompute = conn.execute(
            "SELECT max(last_recomputed) FROM chunk_scores"
        ).fetchone()[0]
        if last_recompute:
            click.echo(f"  Last recompute: {last_recompute[:19]}")


@cli.command(name="coverage-map")
@click.option("--format", "fmt", type=click.Choice(["terminal", "json", "vault", "all"]), default="terminal", help="Output format.")
@click.option("--config", type=click.Path(exists=True), default=None, help="Path to coverage_domains.toml.")
@click.option("--vault-path", type=click.Path(), default=None, help="Override vault note output path.")
@click.option("--check-threshold", is_flag=True, help="Check if re-run is needed based on row growth, then exit.")
@click.pass_context
def coverage_map_cmd(ctx: click.Context, fmt: str, config: str | None, vault_path: str | None, check_threshold: bool) -> None:
    """Generate the substrate coverage map (year x life-domain density)."""
    from pathlib import Path as P

    from phdb.db import connect
    from phdb.tools.coverage_map import (
        generate_coverage_map,
        load_config,
        render_terminal,
        should_rerun,
        write_json,
        write_state,
        write_vault_note,
    )

    settings = ctx.obj["settings"]
    config_path = P(config) if config else None
    cfg = load_config(config_path)

    instance_dir = P(settings.instance_dir) if settings.instance_dir else P(settings.db_path).parent
    json_path = instance_dir / "coverage_map.json"
    state_path = instance_dir / "coverage_state.json"

    if check_threshold:
        with connect(settings.db_path, readonly=True) as conn:
            total = conn.execute(
                "SELECT SUM(c) FROM ("
                "SELECT count(*) c FROM observations UNION ALL "
                "SELECT count(*) FROM chat_messages UNION ALL "
                "SELECT count(*) FROM emails UNION ALL "
                "SELECT count(*) FROM exercise_actions UNION ALL "
                "SELECT count(*) FROM actions"
                ")"
            ).fetchone()[0] or 0
        if should_rerun(state_path, total):
            click.echo("Coverage map re-run recommended (threshold exceeded).")
            raise SystemExit(0)
        else:
            click.echo("Coverage map is current (below threshold).")
            raise SystemExit(1)

    with connect(settings.db_path, readonly=True) as conn:
        data = generate_coverage_map(conn, config=cfg)

    if fmt in ("terminal", "all"):
        click.echo(render_terminal(data))

    if fmt in ("json", "all"):
        write_json(data, json_path)
        click.echo(f"JSON written to: {json_path}")

    if fmt in ("vault", "all"):
        vp = P(vault_path) if vault_path else P("Substrate Coverage Map.md")
        write_vault_note(data, vp)
        click.echo(f"Vault note written to: {vp}")

    write_state(data, state_path)
    click.echo(f"State written to: {state_path}")


# ---------------------------------------------------------------------------
# Plugin management — Phase 3 of the phdb Plugin Architecture plan
# ---------------------------------------------------------------------------


@cli.group()
def plugin() -> None:
    """Plugin discovery + introspection."""


@plugin.command(name="list")
@click.option("--include-facets/--no-facets", default=True, help="Include facet plugins.")
def plugin_list(include_facets: bool) -> None:
    """List installed plugins (source + facet)."""
    from phdb.core.plugin import discover_facets, discover_plugins

    source = discover_plugins()
    facets = discover_facets() if include_facets else []
    if not source and not facets:
        click.echo("No plugins installed.")
        return
    if source:
        click.echo("Source plugins:")
        for d in source:
            issue_marker = f"  [!] {len(d.issues)} issue(s)" if d.issues else ""
            click.echo(f"  {d.name:30s} v{d.manifest.version:8s}  {d.source}{issue_marker}")
    if facets:
        click.echo("\nFacet plugins:")
        for d in facets:
            issue_marker = f"  [!] {len(d.issues)} issue(s)" if d.issues else ""
            click.echo(f"  {d.name:30s} v{d.manifest.version:8s}  {d.source}{issue_marker}")


@plugin.command(name="describe")
@click.argument("name")
def plugin_describe(name: str) -> None:
    """Show full manifest for a named plugin."""
    from phdb.core.plugin import discover_facets, discover_plugins

    found = next(
        (d for d in discover_plugins() + discover_facets() if d.name == name),
        None,
    )
    if found is None:
        click.echo(f"No plugin named {name!r}. Run `phdb plugin list` to see installed plugins.")
        raise SystemExit(1)
    m = found.manifest
    click.echo(f"Plugin: {m.name}")
    click.echo(f"  Version:     {m.version}")
    click.echo(f"  Kind:        {m.kind}")
    click.echo(f"  Entry point: {m.entry_point}")
    click.echo(f"  Description: {m.description}")
    click.echo(f"  Source:      {found.source} (distribution: {found.distribution or '(in-tree)'})")
    if m.source is not None:
        click.echo("  emits:        " + (", ".join(m.source.emits) or "(none)"))
        click.echo("  entity_refs:  " + (", ".join(m.source.entity_refs) or "(none)"))
        click.echo("  formats_used: " + (", ".join(m.source.formats_used) or "(none)"))
        click.echo("  sidecars:     " + (", ".join(m.source.sidecars) or "(none)"))
    if m.facet is not None:
        click.echo(f"  consumes:    {m.facet.consumes}")
        click.echo(f"  node_table:  {m.facet.node_table}")
    if found.issues:
        click.echo("\nValidation issues:")
        for issue in found.issues:
            click.echo(f"  - {issue}")


@plugin.command(name="ingest")
@click.argument("name")
@click.argument("source", type=click.Path(exists=True))
@click.option("--no-schema-regen", is_flag=True, help="Skip the post-ingest DB_SCHEMA.md regen hook.")
@click.pass_context
def plugin_ingest(ctx: click.Context, name: str, source: str, no_schema_regen: bool) -> None:
    """Ingest a source file via the named plugin (Phase 5+ CLI)."""
    from pathlib import Path as P

    from phdb.core.plugin import discover_facets, discover_plugins, load_plugin
    from phdb.db import connect
    from phdb.writelock import write_lock

    descriptors = discover_plugins() + discover_facets()
    descriptor = next((d for d in descriptors if d.name == name), None)
    if descriptor is None:
        click.echo(f"No plugin named {name!r}. Run `phdb plugin list`.")
        raise SystemExit(1)
    if descriptor.issues:
        click.echo(f"Plugin {name!r} has validation issues:")
        for issue in descriptor.issues:
            click.echo(f"  - {issue}")
        raise SystemExit(1)

    settings = ctx.obj["settings"]
    plugin_obj = load_plugin(descriptor)
    run_fn = getattr(plugin_obj, "run", None)
    if run_fn is None:
        click.echo(f"Plugin {name!r} does not expose a run() method.")
        raise SystemExit(1)

    with write_lock(settings.db_path), connect(settings.db_path) as conn:
        report = run_fn(P(source), conn, settings)
    rows_inserted = getattr(report, "rows_inserted", "?")
    rows_yielded = getattr(report, "rows_yielded", "?")
    click.echo(f"[{name}] Done: yielded={rows_yielded} inserted={rows_inserted}")

    if not no_schema_regen:
        _run_schema_regen_hook(settings)


@plugin.command(name="scaffold")
@click.argument("name")
@click.option("--description", default="", help="One-line plugin description.")
@click.option("--emits", default="", help="Comma-separated Schema.org @type strings the plugin emits.")
@click.option("--entity-refs", default="", help="Comma-separated entity table names this plugin's actions FK to.")
@click.option("--formats-used", default="", help="Comma-separated phdb.formats module names this plugin imports.")
@click.option("--facets-projected", default="", help="Comma-separated facet types this plugin projects into.")
@click.option("--force", is_flag=True, help="Overwrite an existing plugin directory.")
def plugin_scaffold(
    name: str,
    description: str,
    emits: str,
    entity_refs: str,
    formats_used: str,
    facets_projected: str,
    force: bool,
) -> None:
    """Scaffold a new plugin skeleton at src/phdb/plugins/<name>/."""
    from phdb.core.plugin.scaffold import (
        ScaffoldError,
        _split_csv,
        scaffold_plugin,
    )

    try:
        result = scaffold_plugin(
            name,
            description=description,
            emits=_split_csv(emits),
            entity_refs=_split_csv(entity_refs),
            formats_used=_split_csv(formats_used),
            facets_projected=_split_csv(facets_projected),
            force=force,
        )
    except ScaffoldError as e:
        click.echo(f"Error: {e}", err=True)
        raise SystemExit(1) from e

    click.echo(f"Scaffolded plugin {name!r}:")
    for path in result.all_paths():
        click.echo(f"  Created {path}")
    click.echo(
        "\nNext steps:\n"
        "  1. Edit plugin.py to implement discover/parse/ingest_row.\n"
        f"  2. Add tests at tests/test_{name}_plugin.py.\n"
        "  3. Run `phdb plugin describe " + name + "` to verify the manifest.\n"
    )
    click.echo(f"Manifest: {result.manifest_path}")


def _run_schema_regen_hook(settings) -> None:  # type: ignore[no-untyped-def]
    """Post-ingest hook — regenerate DB_SCHEMA.md unless suppressed.

    Failures are logged + swallowed; ingest already succeeded, no need
    to fail the command.
    """
    from pathlib import Path as P

    from phdb.db import connect
    from phdb.tools.schema_doc import DEFAULT_OUTPUT_PATH, regenerate

    try:
        if not settings.db_path or not P(settings.db_path).exists():
            content = regenerate(None)
        else:
            with connect(settings.db_path, readonly=True) as conn:
                content = regenerate(conn)
        DEFAULT_OUTPUT_PATH.write_text(content, encoding="utf-8")
        click.echo(f"  schema doc regenerated -> {DEFAULT_OUTPUT_PATH}")
    except Exception as e:
        click.echo(f"  schema doc regen skipped: {e}", err=True)


# ---------------------------------------------------------------------------
# Schema regeneration — Phase 6 of the phdb Plugin Architecture plan
# ---------------------------------------------------------------------------


@cli.group()
def schema() -> None:
    """Schema introspection + regeneration."""


@schema.command(name="regenerate")
@click.option("--output", "-o", default=None, help="Output path (default: DB_SCHEMA.md at cwd).")
@click.option("--dry-run", is_flag=True, help="Print to stdout instead of writing.")
@click.option("--no-counts", is_flag=True, help="Skip live-DB row counts.")
@click.pass_context
def schema_regenerate(ctx: click.Context, output: str | None, dry_run: bool, no_counts: bool) -> None:
    """Regenerate DB_SCHEMA.md from the phdb.schemas registry + live sqlite_master."""
    from pathlib import Path as P

    from phdb.db import connect
    from phdb.tools.schema_doc import DEFAULT_OUTPUT_PATH, regenerate

    settings = ctx.obj["settings"]

    conn = None
    content: str
    if no_counts:
        content = regenerate(None)
    else:
        if not settings.db_path or not P(settings.db_path).exists():
            click.echo(
                "DB not found — regenerating structure-only output. "
                "Pass --no-counts to suppress this message."
            )
            content = regenerate(None)
        else:
            with connect(settings.db_path, readonly=True) as conn:
                content = regenerate(conn)

    if dry_run:
        click.echo(content)
        return
    target = P(output) if output else DEFAULT_OUTPUT_PATH
    target.write_text(content, encoding="utf-8")
    click.echo(f"Wrote {len(content):,} bytes to {target}")


@schema.command(name="diff")
@click.pass_context
def schema_diff(ctx: click.Context) -> None:
    """Show drift between phdb.schemas declarations and the live DB."""
    from phdb.db import connect
    from phdb.tools.schema_doc import diff_against_live

    settings = ctx.obj["settings"]
    with connect(settings.db_path, readonly=True) as conn:
        drift = diff_against_live(conn)
    if not drift:
        click.echo("Schema is clean — no drift between declarations and live DB.")
        return
    for line in drift:
        click.echo(line)


# ---------------------------------------------------------------------------
# Facet review CLI — Phase 8C of the phdb Plugin Architecture plan
# ---------------------------------------------------------------------------


# Facet metadata table — keyed by short facet name (matches plugin.toml
# `[plugin] name`). Each entry names the node_table the apply_merge call
# operates on, the facet_type Schema.org @type tag, and the FK-columns
# constant module path. Phase 8C ships people + places; new facet plugins
# extend this map.
_FACET_REGISTRY: dict[str, dict[str, str]] = {
    "people": {
        "node_table": "persons",
        "facet_type": "Person",
        "fk_columns_module": "phdb.facets.people.coalescence",
        "fk_columns_attr": "PEOPLE_FK_COLUMNS",
    },
    "places": {
        "node_table": "places",
        "facet_type": "Place",
        "fk_columns_module": "phdb.facets.places.coalescence",
        "fk_columns_attr": "PLACES_FK_COLUMNS",
    },
}


def _resolve_fk_columns(facet_name: str) -> list[tuple[str, str]]:
    """Import the facet's coalescence module and read its FK-columns constant.

    Returns ``[]`` when the module or attribute is missing (Phase 8B's
    places module may not exist yet when this CLI ships; the sibling
    can add the constant later without breaking the CLI).
    """
    import importlib

    meta = _FACET_REGISTRY.get(facet_name)
    if not meta:
        return []
    try:
        mod = importlib.import_module(meta["fk_columns_module"])
    except ImportError:
        return []
    fks = getattr(mod, meta["fk_columns_attr"], None)
    if not isinstance(fks, list):
        return []
    return [tuple(t) for t in fks if isinstance(t, tuple) and len(t) == 2]


def _resolve_instance_dir(settings: Any, override: str | None) -> Path | None:
    """Resolve the per-instance config dir from --instance-dir or settings."""
    if override:
        return Path(override)
    if getattr(settings, "instance_dir", None):
        return Path(settings.instance_dir)
    return None


@cli.group()
def facet() -> None:
    """Facet coalescence review + audit commands (Phase 8C)."""


@cli.group(name="facets")
def facets_group() -> None:
    """Cross-facet aggregate commands (e.g., stats)."""


def _format_proposal(proposal_idx: int, total: int, proposal: Any) -> str:
    """Render one proposal as a scannable block for the interactive prompt."""
    from phdb.core.plugin.bus import FacetEmission

    lines = [
        f"\n[{proposal_idx}/{total}] rule={proposal.rule}  "
        f"confidence={proposal.confidence:.2f}  "
        f"survivor={proposal.into_node_id}",
    ]
    payload = proposal.payload or {}
    if payload:
        shape = payload.get("shape", "?")
        ec = payload.get("emission_count", "?")
        xc = payload.get("existing_count", "?")
        lines.append(f"  shape={shape}  emissions={ec}  existing={xc}")
    lines.append(f"  from_emissions ({len(proposal.from_emissions)}):")
    for i, emission in enumerate(proposal.from_emissions, 1):
        if isinstance(emission, FacetEmission):
            src = f"{emission.source_table}#{emission.source_id}"
            payload_repr = ", ".join(
                f"{k}={v!r}"
                for k, v in (emission.payload or {}).items()
                if k != "id"
            )
        elif isinstance(emission, dict):
            src = f"{emission.get('source_table', '?')}#{emission.get('source_id', '?')}"
            inner_payload = emission.get("payload", {}) or {}
            if inner_payload:
                payload_repr = ", ".join(
                    f"{k}={v!r}" for k, v in inner_payload.items() if k != "id"
                )
            else:
                payload_repr = ", ".join(
                    f"{k}={v!r}" for k, v in emission.items()
                    if k not in {"source_table", "source_id", "facet_type", "payload"}
                )
        else:
            src = repr(emission)
            payload_repr = ""
        lines.append(f"    {i}. {src}  {payload_repr}")
    return "\n".join(lines)


def _review_loop(  # noqa: PLR0913 — interactive CLI helper
    ctx: click.Context,
    facet_name: str,
    *,
    auto_accept_threshold: float | None,
    limit: int | None,
    rule_filter: str | None,
    dry_run: bool,
    instance_dir_override: str | None,
) -> None:
    """Walk the pending-review queue for a facet, dispatching per answer."""
    from phdb.db import connect
    from phdb.facets._coalescence_lib import apply_merge
    from phdb.facets._review_queue import load_pending, save_pending

    settings = ctx.obj["settings"]
    meta = _FACET_REGISTRY.get(facet_name)
    if meta is None:
        click.echo(
            f"Unknown facet {facet_name!r}. Known facets: "
            + ", ".join(sorted(_FACET_REGISTRY))
        )
        raise SystemExit(1)

    inst_dir = _resolve_instance_dir(settings, instance_dir_override)
    if inst_dir is None:
        click.echo(
            "No --instance-dir set and no instance config discovered. "
            "Pass --instance-dir <path> or run from within a directory "
            "that has personal-history-instance/."
        )
        raise SystemExit(1)

    proposals = load_pending(facet_name, inst_dir)
    if rule_filter:
        proposals = [p for p in proposals if p.rule == rule_filter]
    if not proposals:
        click.echo(f"No pending proposals for facet {facet_name!r}.")
        return

    fk_columns = _resolve_fk_columns(facet_name)
    accepted: list[Any] = []
    rejected: list[Any] = []
    deferred: list[Any] = []
    stopped_early = False

    work = proposals if limit is None else proposals[:limit]
    total = len(work)

    if dry_run:
        click.echo(f"[dry-run] Would review {total} proposal(s) for {facet_name!r}.")

    def _process(conn: sqlite3.Connection | None) -> None:
        nonlocal stopped_early
        for i, proposal in enumerate(work, 1):
            click.echo(_format_proposal(i, total, proposal))

            # Auto-accept above threshold (per-invocation override).
            if (
                auto_accept_threshold is not None
                and proposal.confidence >= auto_accept_threshold
            ):
                click.echo(f"  [auto-accept @ {auto_accept_threshold:.2f}]")
                if dry_run or conn is None:
                    accepted.append(proposal)
                    continue
                survivor = apply_merge(
                    conn,
                    node_table=meta["node_table"],
                    proposal=proposal,
                    facet_type=meta["facet_type"],
                    fk_columns=fk_columns,
                )
                accepted.append(proposal)
                click.echo(f"  -> merged into id={survivor}")
                continue

            answer = click.prompt(
                "  [a]ccept / [r]eject / [d]efer / [s]top",
                default="d",
                show_default=True,
                type=click.Choice(["a", "r", "d", "s"], case_sensitive=False),
            ).lower()
            if answer == "s":
                stopped_early = True
                # Remaining items (including this one) default to deferred.
                deferred.extend(work[i - 1 :])
                break
            if answer == "a":
                if dry_run or conn is None:
                    click.echo("  [dry-run] would accept")
                    accepted.append(proposal)
                    continue
                try:
                    survivor = apply_merge(
                        conn,
                        node_table=meta["node_table"],
                        proposal=proposal,
                        facet_type=meta["facet_type"],
                        fk_columns=fk_columns,
                    )
                except Exception as exc:
                    click.echo(f"  ! apply_merge failed: {exc}")
                    deferred.append(proposal)
                    continue
                accepted.append(proposal)
                click.echo(f"  -> merged into id={survivor}")
            elif answer == "r":
                if dry_run:
                    click.echo("  [dry-run] would reject")
                rejected.append(proposal)
            else:
                if dry_run:
                    click.echo("  [dry-run] would defer")
                deferred.append(proposal)

    if dry_run:
        _process(None)
    else:
        with connect(settings.db_path) as conn:
            _process(conn)

    # Anything beyond the limit window stays in the queue untouched.
    if limit is not None and len(proposals) > limit:
        deferred.extend(proposals[limit:])

    # Rewrite the queue: deferred only (accepted are now in audit log;
    # rejected are dropped on the operator's say-so).
    if not dry_run:
        save_pending(facet_name, inst_dir, deferred)

    click.echo()
    click.echo(
        f"Done. accepted={len(accepted)}  rejected={len(rejected)}  "
        f"deferred={len(deferred)}"
        + ("  (stopped early)" if stopped_early else "")
    )
    if dry_run:
        click.echo("[dry-run] No DB writes; queue file unchanged.")


def _unmerge_command(
    ctx: click.Context, facet_name: str, audit_id: int,
) -> None:
    """Shared body for ``phdb facet <facet> unmerge <audit_id>``."""
    from phdb.db import connect
    from phdb.facets._coalescence_lib import unmerge

    settings = ctx.obj["settings"]
    meta = _FACET_REGISTRY.get(facet_name)
    if meta is None:
        click.echo(f"Unknown facet {facet_name!r}.")
        raise SystemExit(1)

    with connect(settings.db_path) as conn:
        try:
            summary = unmerge(conn, meta["node_table"], audit_id)
        except ValueError as exc:
            click.echo(f"unmerge failed: {exc}")
            raise SystemExit(1) from exc

    click.echo(f"Unmerged audit_id={summary['audit_id']}")
    click.echo(f"  survivor_id:   {summary['survivor_id']}")
    click.echo(f"  restored_ids:  {summary['restored_ids']}")
    click.echo(f"  restored_count:{summary['restored_count']}")
    click.echo(f"  node_table:    {summary['node_table']}")
    click.echo(f"  note: {summary['note']}")


# --- people subcommands ----------------------------------------------------


@facet.group()
def people() -> None:
    """People facet — Person identity coalescence commands."""


@people.command(name="review")
@click.option(
    "--auto-accept-threshold", type=float, default=None,
    help="Auto-accept any proposal whose confidence >= this value.",
)
@click.option(
    "--limit", type=int, default=None,
    help="Review at most N proposals this session.",
)
@click.option(
    "--rule", "rule_filter", type=str, default=None,
    help="Only review proposals generated by the named rule.",
)
@click.option(
    "--dry-run", is_flag=True,
    help="Preview accept/reject decisions; no DB writes or queue mutation.",
)
@click.option(
    "--instance-dir", "instance_dir_override", type=click.Path(),
    default=None, help="Override the per-instance config dir.",
)
@click.pass_context
def people_review(  # noqa: PLR0913 — Click-mapped flags
    ctx: click.Context,
    auto_accept_threshold: float | None,
    limit: int | None,
    rule_filter: str | None,
    dry_run: bool,
    instance_dir_override: str | None,
) -> None:
    """Interactively walk pending merge proposals for the people facet."""
    _review_loop(
        ctx, "people",
        auto_accept_threshold=auto_accept_threshold,
        limit=limit,
        rule_filter=rule_filter,
        dry_run=dry_run,
        instance_dir_override=instance_dir_override,
    )


@people.command(name="unmerge")
@click.argument("audit_id", type=int)
@click.pass_context
def people_unmerge(ctx: click.Context, audit_id: int) -> None:
    """Reverse a single audit entry on the persons table."""
    _unmerge_command(ctx, "people", audit_id)


# --- places subcommands ----------------------------------------------------


@facet.group()
def places() -> None:
    """Places facet — geographic place coalescence commands."""


@places.command(name="review")
@click.option(
    "--auto-accept-threshold", type=float, default=None,
    help="Auto-accept any proposal whose confidence >= this value.",
)
@click.option(
    "--limit", type=int, default=None,
    help="Review at most N proposals this session.",
)
@click.option(
    "--rule", "rule_filter", type=str, default=None,
    help="Only review proposals generated by the named rule.",
)
@click.option(
    "--dry-run", is_flag=True,
    help="Preview accept/reject decisions; no DB writes or queue mutation.",
)
@click.option(
    "--instance-dir", "instance_dir_override", type=click.Path(),
    default=None, help="Override the per-instance config dir.",
)
@click.pass_context
def places_review(  # noqa: PLR0913 — Click-mapped flags
    ctx: click.Context,
    auto_accept_threshold: float | None,
    limit: int | None,
    rule_filter: str | None,
    dry_run: bool,
    instance_dir_override: str | None,
) -> None:
    """Interactively walk pending merge proposals for the places facet."""
    _review_loop(
        ctx, "places",
        auto_accept_threshold=auto_accept_threshold,
        limit=limit,
        rule_filter=rule_filter,
        dry_run=dry_run,
        instance_dir_override=instance_dir_override,
    )


@places.command(name="unmerge")
@click.argument("audit_id", type=int)
@click.pass_context
def places_unmerge(ctx: click.Context, audit_id: int) -> None:
    """Reverse a single audit entry on the places table."""
    _unmerge_command(ctx, "places", audit_id)


# --- aggregate stats -------------------------------------------------------


def _confidence_bucket(c: float) -> str:
    """Bucket a confidence score for the stats summary."""
    if c >= 0.90:
        return "0.90+"
    if c >= 0.75:
        return "0.75-0.89"
    if c >= 0.50:
        return "0.50-0.74"
    return "<0.50"


@facets_group.command(name="stats")
@click.option(
    "--instance-dir", "instance_dir_override", type=click.Path(),
    default=None, help="Override the per-instance config dir for pending counts.",
)
@click.pass_context
def facets_stats(
    ctx: click.Context, instance_dir_override: str | None,
) -> None:
    """Summarize facet_coalescence_log + pending-review queue depths."""
    from phdb.db import connect
    from phdb.facets._review_queue import load_pending

    settings = ctx.obj["settings"]
    with connect(settings.db_path, readonly=True) as conn:
        try:
            total = conn.execute(
                "SELECT COUNT(*) FROM facet_coalescence_log"
            ).fetchone()[0]
        except Exception:
            click.echo(
                "facet_coalescence_log table not found. "
                "Run `phdb migrate` first."
            )
            raise SystemExit(1) from None

        by_facet = conn.execute(
            "SELECT facet_type, COUNT(*) FROM facet_coalescence_log "
            "GROUP BY facet_type ORDER BY facet_type"
        ).fetchall()
        by_rule = conn.execute(
            "SELECT rule_name, COUNT(*) FROM facet_coalescence_log "
            "GROUP BY rule_name ORDER BY COUNT(*) DESC"
        ).fetchall()
        conf_rows = conn.execute(
            "SELECT confidence FROM facet_coalescence_log"
        ).fetchall()

    click.echo(f"Facet coalescence — total merges: {total}\n")

    click.echo("By facet_type:")
    if by_facet:
        for ftype, count in by_facet:
            click.echo(f"  {ftype or '(null)':20s} {count:>6}")
    else:
        click.echo("  (none)")

    click.echo("\nBy rule:")
    if by_rule:
        for rname, count in by_rule:
            click.echo(f"  {rname or '(null)':40s} {count:>6}")
    else:
        click.echo("  (none)")

    click.echo("\nBy confidence bucket:")
    buckets: dict[str, int] = {}
    for (c,) in conf_rows:
        if c is None:
            continue
        buckets[_confidence_bucket(float(c))] = (
            buckets.get(_confidence_bucket(float(c)), 0) + 1
        )
    if buckets:
        for label in ("0.90+", "0.75-0.89", "0.50-0.74", "<0.50"):
            count = buckets.get(label, 0)
            click.echo(f"  {label:20s} {count:>6}")
    else:
        click.echo("  (none)")

    inst_dir = _resolve_instance_dir(settings, instance_dir_override)
    click.echo("\nPending review (deferred):")
    if inst_dir is None:
        click.echo("  (no instance dir; pass --instance-dir to inspect)")
        return
    for facet_name in sorted(_FACET_REGISTRY):
        pending = load_pending(facet_name, inst_dir)
        click.echo(f"  {facet_name:20s} {len(pending):>6}")
