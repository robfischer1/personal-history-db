"""Facebook Messenger adapter — DEPRECATED, use facebook_unified.

This module exists for backward compatibility. The FacebookAdapter class
is a thin alias for FacebookUnifiedAdapter with name='facebook'.
"""

from __future__ import annotations

from phdb.adapters.facebook_unified import FacebookUnifiedAdapter


class FacebookAdapter(FacebookUnifiedAdapter):
    """Legacy alias — messenger-only entry point."""

    name = "facebook"
    source_kind = "facebook"
