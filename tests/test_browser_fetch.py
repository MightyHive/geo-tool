from browser_fetch import is_bot_wall, is_transport_failure


def test_waf_provider_cloudflare():
    from browser_fetch import waf_provider

    assert waf_provider(403, {"server": "cloudflare"}, b"") == "cloudflare"


def test_is_bot_wall_cloudflare_headers():
    assert is_bot_wall(
        403,
        {"server": "cloudflare", "cf-mitigated": "challenge"},
        b"<html></html>",
    )


def test_is_bot_wall_plain_403_is_not_bot_wall():
    assert not is_bot_wall(403, {"server": "nginx"}, b"Forbidden")


def test_is_bot_wall_challenge_body():
    assert is_bot_wall(
        503,
        {},
        b'<script src="https://challenges.cloudflare.com/turnstile/v0/api.js"></script>',
    )


def test_transport_timeout_is_browser_fallback_candidate():
    assert is_transport_failure(None, "The read operation timed out")


def test_http_error_is_not_transport_failure():
    assert not is_transport_failure(403, "HTTP Error 403: Forbidden")


def test_failed_browser_session_is_reset(monkeypatch):
    import browser_fetch

    class FailedSession:
        closed = False

        def fetch(self, _url):
            raise RuntimeError("browser executable unavailable")

        def close(self):
            self.closed = True

    session = FailedSession()
    monkeypatch.setattr(browser_fetch, "_session", session)

    result = browser_fetch.fetch_with_browser_fallback(
        "https://example.com",
        status=None,
        headers=None,
        error="timed out",
    )

    assert result is None
    assert session.closed is True
    assert browser_fetch._session is None


def test_resolve_status_real_html_after_403():
    from browser_fetch import _resolve_status

    html = "<!DOCTYPE html><html><head><title>La Roche-Posay</title></head><body>Shop</body></html>"
    assert _resolve_status(403, html) == 200


def test_resolve_status_challenge_html_stays_403():
    from browser_fetch import _resolve_status

    html = '<html><script src="https://challenges.cloudflare.com/turnstile/v0/api.js"></script></html>'
    assert _resolve_status(403, html) == 403
