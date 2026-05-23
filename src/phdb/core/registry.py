"""Runtime registry of typed tables, date columns, and embeddable bodies.

Phase 1 holding pen for the table inventories that were previously
hardcoded across ``embed_pipeline.py`` (``_EMBEDDABLE_TABLES``),
``scoring.py`` (``_DATE_COLUMN``), and ``query.py`` (``_TYPED_TABLES``,
``_COMM_TABLES``). Phase 3 swaps the hardcoded defaults for entries fed
from plugin manifests; the surface this module exposes is stable across
that switch.
"""

from __future__ import annotations

from dataclasses import dataclass, field


@dataclass(frozen=True)
class TypedTableSpec:
    """Static descriptor for a typed table the framework knows about."""

    name: str
    date_column: str | None = None
    embeddable: bool = False
    embed_body_column: str = "body_text"
    embed_schema_type: str | None = None
    schema_type: str | None = None
    is_communication: bool = False


# ---------------------------------------------------------------------------
# Canonical inventory — kept in sync with the live DB schema (28 typed tables
# as of migration 0023). Phase 3 will rebuild this from plugin manifests; the
# tuple shape stays the same.
# ---------------------------------------------------------------------------
_DEFAULT_TYPED_TABLES: tuple[TypedTableSpec, ...] = (
    TypedTableSpec("emails", "date_sent", embeddable=True,
                   embed_schema_type="EmailMessage", schema_type="EmailMessage",
                   is_communication=True),
    TypedTableSpec("chat_messages", "date_sent", embeddable=True,
                   embed_schema_type="Message", schema_type="Message",
                   is_communication=True),
    TypedTableSpec("conversations_messages", "date_sent", embeddable=True,
                   embed_schema_type="Conversation", schema_type="Conversation",
                   is_communication=True),
    TypedTableSpec("observations", "date_observed", schema_type="Observation"),
    TypedTableSpec("exercise_actions", "date_performed", schema_type="ExerciseAction"),
    TypedTableSpec("search_actions", "date_performed", schema_type="SearchAction"),
    TypedTableSpec("listen_actions", "date_listened", schema_type="ListenAction"),
    TypedTableSpec("watch_actions", "date_watched", schema_type="WatchAction"),
    TypedTableSpec("actions", "date_performed", schema_type="Action"),
    TypedTableSpec("events", "date_occurred", schema_type="Event"),
    TypedTableSpec("products", "date_recorded", schema_type="Product"),
    TypedTableSpec("order_actions", "date_ordered", schema_type="OrderAction"),
    TypedTableSpec("like_actions", "date_liked", schema_type="LikeAction"),
    TypedTableSpec("persons", "date_recorded", schema_type="Person"),
    TypedTableSpec("social_postings", "date_posted", schema_type="SocialPosting"),
    TypedTableSpec("comments", "date_posted", schema_type="Comment"),
    TypedTableSpec("places", "date_recorded", schema_type="Place"),
    TypedTableSpec("travel_actions", "date_traveled", schema_type="TravelAction"),
    TypedTableSpec("geo_shapes", "date_recorded", schema_type="GeoShape"),
    TypedTableSpec("books", "date_recorded", schema_type="Book"),
    TypedTableSpec("medical_records", "date_recorded", schema_type="MedicalRecord"),
    TypedTableSpec("reviews", "date_reviewed", schema_type="Review"),
    TypedTableSpec("invite_actions", "date_invited", schema_type="InviteAction"),
    TypedTableSpec("creative_works", "date_created", schema_type="CreativeWork"),
    TypedTableSpec("web_pages", "date_recorded", schema_type="WebPage"),
    TypedTableSpec("join_actions", "date_joined", schema_type="JoinAction"),
    TypedTableSpec("digital_documents", "date_created", schema_type="DigitalDocument"),
    TypedTableSpec("things", "date_recorded", schema_type="Thing"),
)


# Document-shaped tables — chunkable bodies that aren't in _TYPED_TABLES but
# do participate in embed + search hydration. The original embed_pipeline
# branched on these three names; Phase 3 will feed them via plugin manifests.
_DEFAULT_DOCUMENT_TABLES: tuple[TypedTableSpec, ...] = (
    TypedTableSpec("documents", "mtime", embeddable=True,
                   embed_schema_type="DigitalDocument"),
    TypedTableSpec("articles", "mtime", embeddable=True,
                   embed_schema_type="Article"),
    TypedTableSpec("clippings", "mtime", embeddable=True,
                   embed_schema_type="Quotation"),
)


@dataclass
class Registry:
    """Single source of truth for typed-table inventories.

    Phase 1: hand-seeded from the constants above. Phase 3 will replace
    ``_DEFAULT_TYPED_TABLES`` with plugin-manifest-driven entries via
    ``register_table()``.
    """

    typed_tables: dict[str, TypedTableSpec] = field(default_factory=dict)
    document_tables: dict[str, TypedTableSpec] = field(default_factory=dict)

    @classmethod
    def default(cls) -> Registry:
        return cls(
            typed_tables={t.name: t for t in _DEFAULT_TYPED_TABLES},
            document_tables={t.name: t for t in _DEFAULT_DOCUMENT_TABLES},
        )

    def register_table(self, spec: TypedTableSpec) -> None:
        """Add or overwrite a typed-table spec. Used by plugin manifests (Phase 3+)."""
        self.typed_tables[spec.name] = spec

    # ---- Query helpers (replace the hardcoded inventories) ----

    @property
    def typed_table_names(self) -> list[str]:
        return list(self.typed_tables)

    @property
    def comm_table_names(self) -> list[str]:
        return [t.name for t in self.typed_tables.values() if t.is_communication]

    @property
    def embeddable_communication_tables(self) -> list[tuple[str, str]]:
        """[(table_name, schema_type)] for communication tables that embed."""
        return [
            (t.name, t.embed_schema_type or t.schema_type or "Message")
            for t in self.typed_tables.values()
            if t.is_communication and t.embeddable
        ]

    @property
    def embeddable_document_tables(self) -> list[tuple[str, str]]:
        """[(table_name, schema_type)] for document-shaped embeddable tables."""
        return [
            (t.name, t.embed_schema_type or "DigitalDocument")
            for t in self.document_tables.values()
            if t.embeddable
        ]

    def date_column(self, table: str, default: str = "date_sent") -> str:
        """Look up the canonical date column for a typed table."""
        spec = self.typed_tables.get(table)
        if spec and spec.date_column:
            return spec.date_column
        return default


# Process-wide default. Phase 3 swaps this for a plugin-driven registry that
# the plugin loader builds at import time.
_DEFAULT: Registry | None = None


def default_registry() -> Registry:
    """Return the lazily-built process-wide default registry."""
    global _DEFAULT
    if _DEFAULT is None:
        _DEFAULT = Registry.default()
    return _DEFAULT


def reset_default_registry() -> None:
    """Test helper — force the next ``default_registry()`` call to rebuild."""
    global _DEFAULT
    _DEFAULT = None
