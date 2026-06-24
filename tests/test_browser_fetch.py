from browser_fetch import is_bot_wall


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


def test_resolve_status_real_html_after_403():
    from browser_fetch import _resolve_status

    html = "<!DOCTYPE html><html><head><title>La Roche-Posay</title></head><body>Shop</body></html>"
    assert _resolve_status(403, html) == 200


def test_resolve_status_challenge_html_stays_403():
    from browser_fetch import _resolve_status

    html = '<html><script src="https://challenges.cloudflare.com/turnstile/v0/api.js"></script></html>'
    assert _resolve_status(403, html) == 403
