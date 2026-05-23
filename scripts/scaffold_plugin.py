#!/usr/bin/env python3
"""Scaffold a new phdb plugin skeleton.

Thin wrapper around the ``phdb plugin scaffold`` Click command. Use
this when you want a standalone script (e.g., from an editor's
external-tools menu or a vault skill) instead of typing the full
``phdb plugin scaffold`` invocation.

The actual scaffold logic lives in ``phdb.core.plugin.scaffold`` —
this script just hands argv to the CLI's Click group.

Replaces the legacy ``scripts/scaffold_adapter.py`` (deleted in the
same commit) which emitted dead-code adapter scaffolds against the
removed ``Adapter`` ABC.

Usage:
    python scripts/scaffold_plugin.py NAME [click options...]

Example:
    python scripts/scaffold_plugin.py myplug \\
        --description "My plugin" \\
        --emits "Observation" \\
        --formats-used "csv"
"""

from __future__ import annotations

import sys

from phdb.cli import cli


def main() -> None:
    # Re-dispatch into the Click group so the standalone script and the
    # `phdb plugin scaffold` subcommand share one code path.
    argv = ["plugin", "scaffold", *sys.argv[1:]]
    cli.main(args=argv, prog_name="phdb", standalone_mode=True)


if __name__ == "__main__":
    main()
