"""Substrate Coverage Map — year x life-domain density matrix.

Produces a heat-map of corpus density across time and life-domains,
turning "capture everything" into "capture the thin cells."
"""

from __future__ import annotations

import json
import sqlite3
import tomllib
from collections import defaultdict
from datetime import UTC
from pathlib import Path
from typing import Any

from phdb.tools.sparsity import compute_sparsity

CONFIG_PATH = Path(__file__).parents[3] / "config" / "coverage_domains.toml"

CONTRIBUTING_TABLES = {
    "typed_tables": {
        "date_col": "t.dt",
        "has_source_kind": False,
        "has_schema_type": True,
        "has_sender_domain": False,
        "join_source_files": False,
        "query": (
            "SELECT year, source_kind, schema_type, NULL as sender_domain FROM ("
            " SELECT CAST(strftime('%Y', date_observed) AS INTEGER) year, sf.source_kind, schema_type FROM observations LEFT JOIN source_files sf ON source_file_id=sf.id WHERE date_observed IS NOT NULL AND date_observed != ''"
            " UNION ALL SELECT CAST(strftime('%Y', date_sent) AS INTEGER), sf.source_kind, schema_type FROM chat_messages LEFT JOIN source_files sf ON source_file_id=sf.id WHERE date_sent IS NOT NULL AND date_sent != ''"
            " UNION ALL SELECT CAST(strftime('%Y', date_sent) AS INTEGER), sf.source_kind, schema_type FROM emails LEFT JOIN source_files sf ON source_file_id=sf.id WHERE date_sent IS NOT NULL AND date_sent != ''"
            " UNION ALL SELECT CAST(strftime('%Y', date_sent) AS INTEGER), sf.source_kind, schema_type FROM conversations_messages LEFT JOIN source_files sf ON source_file_id=sf.id WHERE date_sent IS NOT NULL AND date_sent != ''"
            " UNION ALL SELECT CAST(strftime('%Y', date_performed) AS INTEGER), sf.source_kind, schema_type FROM exercise_actions LEFT JOIN source_files sf ON source_file_id=sf.id WHERE date_performed IS NOT NULL AND date_performed != ''"
            " UNION ALL SELECT CAST(strftime('%Y', date_performed) AS INTEGER), sf.source_kind, schema_type FROM search_actions LEFT JOIN source_files sf ON source_file_id=sf.id WHERE date_performed IS NOT NULL AND date_performed != ''"
            " UNION ALL SELECT CAST(strftime('%Y', date_listened) AS INTEGER), sf.source_kind, schema_type FROM listen_actions LEFT JOIN source_files sf ON source_file_id=sf.id WHERE date_listened IS NOT NULL AND date_listened != ''"
            " UNION ALL SELECT CAST(strftime('%Y', date_watched) AS INTEGER), sf.source_kind, schema_type FROM watch_actions LEFT JOIN source_files sf ON source_file_id=sf.id WHERE date_watched IS NOT NULL AND date_watched != ''"
            " UNION ALL SELECT CAST(strftime('%Y', date_performed) AS INTEGER), sf.source_kind, schema_type FROM actions LEFT JOIN source_files sf ON source_file_id=sf.id WHERE date_performed IS NOT NULL AND date_performed != ''"
            " UNION ALL SELECT CAST(strftime('%Y', date_event) AS INTEGER), sf.source_kind, schema_type FROM events LEFT JOIN source_files sf ON source_file_id=sf.id WHERE date_event IS NOT NULL AND date_event != ''"
            ")"
        ),
    },
    "photographs": {
        "date_col": "p.captured_at",
        "has_source_kind": True,
        "has_schema_type": False,
        "has_sender_domain": False,
        "join_source_files": False,
        "query": (
            "SELECT CAST(strftime('%Y', p.captured_at) AS INTEGER) as year,"
            " p.source_kind, NULL as schema_type, NULL as sender_domain"
            " FROM photographs p"
            " WHERE p.captured_at IS NOT NULL AND p.captured_at != ''"
        ),
    },
    "workout_events": {
        "date_col": "w.date",
        "has_source_kind": False,
        "has_schema_type": False,
        "has_sender_domain": False,
        "join_source_files": False,
        "query": (
            "SELECT CAST(strftime('%Y', w.date) AS INTEGER) as year,"
            " 'strong' as source_kind, NULL as schema_type, NULL as sender_domain"
            " FROM workout_events w"
            " WHERE w.date IS NOT NULL AND w.date != ''"
        ),
    },
    "documents": {
        "date_col": "d.mtime",
        "has_source_kind": False,
        "has_schema_type": True,
        "has_sender_domain": False,
        "join_source_files": True,
        "query": (
            "SELECT CAST(strftime('%Y', d.mtime) AS INTEGER) as year,"
            " sf.source_kind, d.schema_type, NULL as sender_domain"
            " FROM documents d LEFT JOIN source_files sf ON d.source_file_id = sf.id"
            " WHERE d.mtime IS NOT NULL AND d.mtime != ''"
        ),
    },
    "bookmarks": {
        "date_col": "b.first_seen_in_instrument",
        "has_source_kind": False,
        "has_schema_type": True,
        "has_sender_domain": False,
        "join_source_files": True,
        "query": (
            "SELECT CAST(strftime('%Y', b.first_seen_in_instrument) AS INTEGER) as year,"
            " sf.source_kind, b.schema_type, NULL as sender_domain"
            " FROM bookmarks b LEFT JOIN source_files sf ON b.source_file_id = sf.id"
            " WHERE b.first_seen_in_instrument IS NOT NULL AND b.first_seen_in_instrument != ''"
        ),
    },
    "connections": {
        "date_col": "c.friends_since",
        "has_source_kind": False,
        "has_schema_type": True,
        "has_sender_domain": False,
        "join_source_files": True,
        "query": (
            "SELECT CAST(strftime('%Y', c.friends_since) AS INTEGER) as year,"
            " sf.source_kind, c.schema_type, NULL as sender_domain"
            " FROM connections c LEFT JOIN source_files sf ON c.source_file_id = sf.id"
            " WHERE c.friends_since IS NOT NULL AND c.friends_since != ''"
        ),
    },
}


def load_config(config_path: Path | None = None) -> dict[str, Any]:
    """Load the coverage domains TOML config."""
    path = config_path or CONFIG_PATH
    return tomllib.loads(path.read_text(encoding="utf-8"))


def classify_domain(
    source_kind: str | None,
    schema_type: str | None,
    sender_domain: str | None,
    config: dict[str, Any],
) -> str:
    """Classify a row into a life-domain using the priority ladder.

    Priority: source_kind → schema_type → sender_domain → unclassified.
    Topic/content-type outranks sender metadata (D3).
    """
    rules = config.get("rules", {})

    # Layer 1: source_kind
    if source_kind:
        sk_rules = rules.get("source_kind", {})
        if source_kind in sk_rules:
            return str(sk_rules[source_kind])

    # Layer 2: schema_type
    if schema_type:
        st_rules = rules.get("schema_type", {})
        if schema_type in st_rules:
            domain = str(st_rules[schema_type])
            # Layer 3: sender_domain can override for EmailMessage
            if schema_type == "EmailMessage" and sender_domain:
                sd_rules = rules.get("sender_domain", {})
                if sender_domain in sd_rules:
                    return str(sd_rules[sender_domain])
            return domain

    # Layer 3 fallback: sender_domain even without schema_type match
    if sender_domain:
        sd_rules = rules.get("sender_domain", {})
        if sender_domain in sd_rules:
            return str(sd_rules[sender_domain])

    return "unclassified"


def query_coverage(conn: sqlite3.Connection, config: dict[str, Any]) -> list[dict[str, Any]]:
    """Query all contributing tables and classify into year x domain cells.

    Each table has a pre-built SQL query returning: year, source_kind, schema_type, sender_domain.
    """
    counts: dict[tuple[int, str], int] = defaultdict(int)

    for _table_name, meta in CONTRIBUTING_TABLES.items():
        sql = str(meta["query"])

        try:
            cursor = conn.execute(sql)
        except sqlite3.OperationalError:
            continue

        for row in cursor:
            year = row[0]
            if not year or year < 1980 or year > 2030:
                continue

            source_kind = row[1]
            schema_type = row[2]
            sender_domain = row[3]
            if sender_domain:
                sender_domain = sender_domain.lower().strip()

            domain = classify_domain(source_kind, schema_type, sender_domain, config)
            counts[(year, domain)] += 1

    # Build cells list
    domains = config["domains"]["names"] + ["unclassified"]
    all_years = sorted(set(y for y, _ in counts)) if counts else []

    cells = []
    for year in all_years:
        for domain in domains:
            count = counts.get((year, domain), 0)
            scale_group = "health" if domain == "health" else "main"
            cells.append({
                "year": year,
                "domain": domain,
                "count": count,
                "scale_group": scale_group,
            })

    return compute_sparsity(cells)


def generate_coverage_map(
    conn: sqlite3.Connection,
    config: dict[str, Any] | None = None,
    config_path: Path | None = None,
) -> dict[str, Any]:
    """Generate the full coverage map data structure."""
    if config is None:
        config = load_config(config_path)

    cells = query_coverage(conn, config)

    total_rows = sum(c["count"] for c in cells)
    non_health = [c for c in cells if c["domain"] != "health"]
    unclassified_count = sum(c["count"] for c in cells if c["domain"] == "unclassified")
    non_health_total = sum(c["count"] for c in non_health)
    unclassified_pct = (unclassified_count / non_health_total * 100) if non_health_total else 0

    # Thinnest cells (non-zero, non-health, sorted by count)
    thinnest = sorted(
        [c for c in cells if c["count"] > 0 and c["domain"] not in ("health", "unclassified")],
        key=lambda c: c["count"],
    )[:10]

    years = sorted(set(c["year"] for c in cells))

    return {
        "generated_at": None,  # caller sets this
        "total_rows": total_rows,
        "total_rows_at_generation": total_rows,
        "unclassified_pct": round(unclassified_pct, 1),
        "domains": config["domains"]["names"],
        "years": years,
        "cells": cells,
        "thinnest_cells": thinnest,
    }


def render_terminal(data: dict[str, Any]) -> str:
    """Render the coverage map as an ANSI-colored terminal table."""
    domains = data["domains"]
    years = data["years"]
    cells_by_key: dict[tuple[int, str], dict[str, Any]] = {}
    for c in data["cells"]:
        cells_by_key[(c["year"], c["domain"])] = c

    # Separate health from main
    main_domains = [d for d in domains if d != "health"]

    def _density_marker(count: int, is_health: bool = False) -> str:
        if is_health:
            thresholds = [(0, "\033[2m    ·   \033[0m"), (100, "\033[33m  ░░░  \033[0m"),
                          (10000, "\033[32m  ▒▒▒  \033[0m"), (100000, "\033[32;1m  ███  \033[0m")]
        else:
            thresholds = [(0, "\033[2m    ·   \033[0m"), (10, "\033[31m  ░░░  \033[0m"),
                          (100, "\033[33m  ▒▒▒  \033[0m"), (1000, "\033[32m  ▓▓▓  \033[0m"),
                          (10000, "\033[32;1m  ███  \033[0m")]

        marker = thresholds[0][1]
        for threshold, m in thresholds:
            if count >= threshold:
                marker = m
        return marker

    lines: list[str] = []

    # Header
    lines.append("\n\033[1mSubstrate Coverage Map\033[0m")
    lines.append(f"Total rows: {data['total_rows']:,}  |  Unclassified: {data['unclassified_pct']:.1f}%\n")

    # Thinnest cells summary
    if data["thinnest_cells"]:
        thin_str = ", ".join(f"{c['year']}/{c['domain']}({c['count']})" for c in data["thinnest_cells"][:5])
        lines.append(f"Thinnest: {thin_str}\n")

    # Main domains table
    col_width = 9
    header = "Year".ljust(6) + "".join(d[:col_width].center(col_width) for d in main_domains) + "unclass".center(col_width)
    lines.append(header)
    lines.append("-" * len(header))

    for year in years:
        row = str(year).ljust(6)
        for domain in main_domains:
            cell = cells_by_key.get((year, domain))
            count = cell["count"] if cell else 0
            row += _density_marker(count)
        # Unclassified
        cell = cells_by_key.get((year, "unclassified"))
        count = cell["count"] if cell else 0
        row += _density_marker(count)
        lines.append(row)

    # Health row (separate scale)
    lines.append("\n\033[1mHealth (separate scale)\033[0m")
    health_header = "Year".ljust(6) + "health".center(col_width)
    lines.append(health_header)
    lines.append("-" * len(health_header))
    for year in years:
        cell = cells_by_key.get((year, "health"))
        count = cell["count"] if cell else 0
        row = str(year).ljust(6) + _density_marker(count, is_health=True)
        lines.append(row)

    # Legend
    lines.append("\n\033[2m·\033[0m=0  \033[31m░░░\033[0m=1-99  \033[33m▒▒▒\033[0m=100-999  \033[32m▓▓▓\033[0m=1K-9.9K  \033[32;1m███\033[0m=10K+")

    return "\n".join(lines)


def render_vault(data: dict[str, Any]) -> str:
    """Render the coverage map as a markdown table for the vault note."""
    domains = data["domains"]
    years = data["years"]
    cells_by_key: dict[tuple[int, str], dict[str, Any]] = {}
    for c in data["cells"]:
        cells_by_key[(c["year"], c["domain"])] = c

    main_domains = [d for d in domains if d != "health"]

    def _md_marker(count: int) -> str:
        if count == 0:
            return " "
        elif count < 10:
            return f"_{count}_"
        elif count < 100:
            return str(count)
        elif count < 1000:
            return f"**{count}**"
        elif count < 10000:
            return f"**{count:,}**"
        else:
            return f"***{count:,}***"

    lines: list[str] = []
    lines.append("---")
    lines.append('"@context": "https://schema.org"')
    lines.append('"@type": "Dataset"')
    lines.append('name: "Substrate Coverage Map"')
    lines.append(f'description: "Year x domain density matrix — {data["total_rows"]:,} total rows across {len(years)} years and {len(domains)} domains."')
    lines.append('identifier: "substrate-coverage-map-rendered"')
    lines.append('created: 2026-05-22')
    lines.append('updated: 2026-05-22')
    lines.append('author_type: "ai-generated"')
    lines.append('status: "Active"')
    lines.append('up: "[[State of Project]]"')
    lines.append("tags:")
    lines.append("---")
    lines.append("")
    lines.append("## Substrate Coverage Map")
    lines.append("")
    lines.append(f"> **{data['total_rows']:,}** total rows | **{data['unclassified_pct']:.1f}%** unclassified | Generated from phdb")
    lines.append("")

    # Thinnest cells
    if data["thinnest_cells"]:
        thin_str = ", ".join(f"`{c['year']}/{c['domain']}` ({c['count']})" for c in data["thinnest_cells"][:5])
        lines.append(f"**Thinnest cells:** {thin_str}")
        lines.append("")

    # Main table
    header = "| Year | " + " | ".join(d.title() for d in main_domains) + " | Unclass |"
    sep = "| :--: | " + " | ".join(":--:" for _ in main_domains) + " | :--: |"
    lines.append(header)
    lines.append(sep)

    for year in years:
        row_parts = [str(year)]
        for domain in main_domains:
            cell = cells_by_key.get((year, domain))
            count = cell["count"] if cell else 0
            row_parts.append(_md_marker(count))
        cell = cells_by_key.get((year, "unclassified"))
        count = cell["count"] if cell else 0
        row_parts.append(_md_marker(count))
        lines.append("| " + " | ".join(row_parts) + " |")

    # Health table
    lines.append("")
    lines.append("### Health (separate scale)")
    lines.append("")
    lines.append("| Year | Count |")
    lines.append("| :--: | :--: |")
    for year in years:
        cell = cells_by_key.get((year, "health"))
        count = cell["count"] if cell else 0
        lines.append(f"| {year} | {_md_marker(count)} |")

    return "\n".join(lines)


def write_json(data: dict[str, Any], output_path: Path) -> None:
    """Write the coverage map JSON to disk."""
    from datetime import datetime

    data["generated_at"] = datetime.now(UTC).isoformat()
    output_path.write_text(json.dumps(data, indent=2), encoding="utf-8")


def write_vault_note(data: dict[str, Any], vault_path: Path) -> None:
    """Write the vault markdown note."""
    content = render_vault(data)
    vault_path.parent.mkdir(parents=True, exist_ok=True)
    vault_path.write_text(content, encoding="utf-8")


def write_state(data: dict[str, Any], state_path: Path) -> None:
    """Write the coverage state file for post-ingest threshold checks."""
    state = {
        "total_rows_at_last_run": data["total_rows"],
        "generated_at": data.get("generated_at"),
    }
    state_path.write_text(json.dumps(state, indent=2), encoding="utf-8")


def should_rerun(state_path: Path, current_total: int, threshold_pct: float = 0.10, threshold_abs: int = 1_000_000) -> bool:
    """Check if the coverage map should be re-run based on row growth.

    Returns True if DB has grown by >= threshold_pct (10%) or threshold_abs (1M rows)
    since the last run.
    """
    if not state_path.exists():
        return True

    state = json.loads(state_path.read_text(encoding="utf-8"))
    last_total = int(state.get("total_rows_at_last_run", 0))
    if last_total == 0:
        return True

    delta = current_total - last_total
    pct_growth = delta / last_total

    return bool(pct_growth >= threshold_pct or delta >= threshold_abs)
