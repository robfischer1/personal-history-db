# Migration rollback scripts

Manual-rescue SQL scripts that undo specific schema migrations. These are
**not auto-applied** by `MigrationRunner` — they exist as a last-resort
recovery tool. Apply by hand against the live DB only after a backup.
