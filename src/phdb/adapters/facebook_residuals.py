"""Facebook residuals adapter — DEPRECATED, use facebook_unified.

This module exists for backward compatibility.
"""

from __future__ import annotations

from phdb.adapters.facebook_unified import FacebookUnifiedAdapter


class FacebookResidualsAdapter(FacebookUnifiedAdapter):
    """Legacy alias — residuals-only entry point."""

    name = "facebook_residuals"
    source_kind = "facebook-residuals"
