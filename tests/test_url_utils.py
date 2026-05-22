"""Tests for phdb.formats.url — shared URL utilities."""

from phdb.formats.url import extract_domain, is_junk, normalize_url, should_skip


class TestNormalizeUrl:
    def test_strips_tracking_params(self):
        raw = "https://example.com/page?id=123&utm_source=twitter&fbclid=abc"
        assert normalize_url(raw) == "https://example.com/page?id=123"

    def test_http_to_https(self):
        assert normalize_url("http://example.com/path") == "https://example.com/path"

    def test_strips_fragment(self):
        assert normalize_url("https://example.com/page#section") == "https://example.com/page"

    def test_strips_trailing_slash(self):
        assert normalize_url("https://example.com/path/") == "https://example.com/path"

    def test_lowercases_scheme_and_host(self):
        assert normalize_url("HTTPS://EXAMPLE.COM/Path") == "https://example.com/Path"

    def test_strips_default_port_80(self):
        assert normalize_url("http://example.com:80/path") == "https://example.com/path"

    def test_strips_default_port_443(self):
        assert normalize_url("https://example.com:443/path") == "https://example.com/path"

    def test_preserves_non_default_port(self):
        assert normalize_url("https://example.com:8080/path") == "https://example.com:8080/path"

    def test_empty_url(self):
        assert normalize_url("") == ""

    def test_preserves_non_tracking_query_params(self):
        raw = "https://example.com/search?q=hello&page=2"
        assert normalize_url(raw) == "https://example.com/search?q=hello&page=2"


class TestIsJunk:
    def test_google_root_is_junk(self):
        assert is_junk("https://www.google.com/") == "junk:google-root"

    def test_gmail_root_is_junk(self):
        assert is_junk("https://gmail.com") == "junk:gmail-root"

    def test_chrome_internal_is_junk(self):
        assert is_junk("chrome://settings/") == "junk:chrome-internal"

    def test_localhost_is_junk(self):
        assert is_junk("http://localhost:3000/") == "junk:localhost"

    def test_normal_url_not_junk(self):
        assert is_junk("https://example.com/article") is None

    def test_empty_is_junk(self):
        assert is_junk("") == "junk:empty-url"


class TestShouldSkip:
    def test_google_search_skipped(self):
        assert should_skip("https://www.google.com/search?q=test") is not None

    def test_google_redirect_skipped(self):
        assert should_skip("https://www.google.com/url?q=https://example.com") is not None

    def test_normal_url_not_skipped(self):
        assert should_skip("https://example.com/article") is None

    def test_empty_not_skipped(self):
        assert should_skip("") is None


class TestExtractDomain:
    def test_basic_https(self):
        assert extract_domain("https://example.com/path/to/page") == "example.com"

    def test_with_subdomain(self):
        assert extract_domain("https://www.github.com/user/repo") == "www.github.com"

    def test_with_port(self):
        assert extract_domain("https://example.com:8080/path") == "example.com:8080"

    def test_normalized_url_no_default_port(self):
        norm = normalize_url("https://example.com:443/path")
        assert extract_domain(norm) == "example.com"

    def test_empty_returns_none(self):
        assert extract_domain("") is None

    def test_none_returns_none(self):
        assert extract_domain(None) is None  # type: ignore[arg-type]

    def test_bare_domain(self):
        assert extract_domain("https://example.com") == "example.com"


class TestBackwardCompat:
    """Verify the re-exports from formats.raindrop still work."""

    def test_raindrop_reexports(self):
        from phdb.formats.raindrop import (
            JUNK_PATTERNS,
            SKIP_URL_PATTERNS,
            TRACKING_PARAMS,
            is_junk,
            normalize_url,
            should_skip,
        )
        assert len(TRACKING_PARAMS) > 0
        assert len(JUNK_PATTERNS) > 0
        assert len(SKIP_URL_PATTERNS) > 0
        assert normalize_url("http://example.com") == "https://example.com"
        assert is_junk("") == "junk:empty-url"
        assert should_skip("https://example.com") is None
