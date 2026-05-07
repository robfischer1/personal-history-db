"""Sanitized logging.

Default mode is structural-only: adapter names, row counts, timing — never
message body content. The PII filter strips identity patterns (addresses,
phones, names) from any log record that slips through.
"""

from __future__ import annotations

import logging
import re
from collections.abc import Sequence


class PiiFilter(logging.Filter):
    """Redact known PII patterns from log messages."""

    def __init__(self, patterns: Sequence[re.Pattern[str]] | None = None) -> None:
        super().__init__()
        self._patterns: list[re.Pattern[str]] = list(patterns or [])

    def add_pattern(self, pattern: re.Pattern[str]) -> None:
        self._patterns.append(pattern)

    def add_literal(self, text: str) -> None:
        if text:
            self._patterns.append(re.compile(re.escape(text), re.IGNORECASE))

    def filter(self, record: logging.LogRecord) -> bool:
        msg = record.getMessage()
        for pat in self._patterns:
            msg = pat.sub("[REDACTED]", msg)
        record.msg = msg
        record.args = None
        return True


_pii_filter: PiiFilter | None = None


def setup_logging(
    level: str = "INFO",
    *,
    sanitize: bool = True,
    pii_literals: Sequence[str] = (),
    pii_patterns: Sequence[re.Pattern[str]] = (),
) -> logging.Logger:
    """Configure the phdb logger.

    Args:
        level: Log level name.
        sanitize: Enable PII filtering.
        pii_literals: Literal strings to redact (e.g. owner names, phones).
        pii_patterns: Compiled regex patterns to redact.
    """
    global _pii_filter

    logger = logging.getLogger("phdb")
    logger.setLevel(getattr(logging, level.upper(), logging.INFO))

    if not logger.handlers:
        handler = logging.StreamHandler()
        handler.setFormatter(
            logging.Formatter("%(asctime)s %(levelname)-5s %(name)s: %(message)s", datefmt="%H:%M:%S")
        )
        logger.addHandler(handler)

    if sanitize:
        _pii_filter = PiiFilter(list(pii_patterns))
        for lit in pii_literals:
            _pii_filter.add_literal(lit)
        logger.addFilter(_pii_filter)

    return logger


def get_logger(name: str = "phdb") -> logging.Logger:
    """Get a child logger under the phdb namespace."""
    return logging.getLogger(name)
