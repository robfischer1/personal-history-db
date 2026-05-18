"""Facebook Posts adapter — DEPRECATED, use facebook_unified.

This module exists for backward compatibility.
"""

from __future__ import annotations

from phdb.adapters.facebook_unified import FacebookUnifiedAdapter


class FacebookPostsAdapter(FacebookUnifiedAdapter):
    """Legacy alias — posts-only entry point."""

    name = "facebook_posts"
    source_kind = "facebook-posts"
