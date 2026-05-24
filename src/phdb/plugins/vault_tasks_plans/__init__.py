"""phdb.plugins.vault_tasks_plans — Vault task/plan dissolution ingester.

Tasks and Projects Dissolution (Outputs/Plans/Tasks and Projects Dissolution.md).
Reads task files from Outputs/Tasks/ + System/Tasks/ and plan files from
Outputs/Plans/ + System/Plans/. Writes one row per file into the ``tasks``
or ``plans`` typed table (migration 0033). Multi-type plugin routing by
``note_type`` frontmatter.
"""

from __future__ import annotations

from phdb.plugins.vault_tasks_plans.plugin import VaultTasksPlansPlugin

__all__ = ["VaultTasksPlansPlugin"]
