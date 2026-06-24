"""Playwright fallback for sites behind Cloudflare / bot challenges."""

from __future__ import annotations

import threading
import time
from collections.abc import Mapping
from typing import Any
from urllib.parse import urlparse

_BROWSER_UA = (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
    "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/121.0.0.0 Safari/537.36"
)
_CHALLENGE_TITLE_MARKERS = ("just a moment", "attention required", "checking your browser")
_CHALLENGE_BODY_MARKERS = (
    "challenges.cloudflare.com",
    "cf-challenge",
    "cdn-cgi/challenge-platform",
    "turnstile",
)
_STEALTH_INIT_SCRIPT = """
Object.defineProperty(navigator, 'webdriver', { get: () => undefined });
window.chrome = window.chrome || { runtime: {} };
"""
_LAUNCH_ARGS = (
    "--no-sandbox",
    "--disable-setuid-sandbox",
    "--disable-blink-features=AutomationControlled",
)

_lock = threading.Lock()
_session: _PlaywrightSession | None = None


def waf_provider(
    status: int | None,
    headers: Mapping[str, str] | None = None,
    body: bytes | str = b"",
) -> str | None:
    """Return a WAF vendor label when the response looks like a bot wall."""
    if status not in (401, 403, 429, 503):
        return None
    hdrs = {k.lower(): v for k, v in (headers or {}).items()}
    if "cloudflare" in (hdrs.get("server") or "").lower() or hdrs.get("cf-ray") or hdrs.get("cf-mitigated"):
        return "cloudflare"
    raw = body if isinstance(body, bytes) else body.encode("utf-8", errors="ignore")
    sample = raw[:12000].lower()
    if any(marker.encode() in sample for marker in _CHALLENGE_BODY_MARKERS):
        return "cloudflare"
    return None


def is_bot_wall(
    status: int | None,
    headers: Mapping[str, str] | None = None,
    body: bytes | str = b"",
) -> bool:
    """True when the response looks like a WAF/bot wall rather than a real page denial."""
    return waf_provider(status, headers, body) is not None


def _looks_like_challenge_html(html: str) -> bool:
    sample = (html or "")[:20000].lower()
    return any(marker in sample for marker in _CHALLENGE_BODY_MARKERS)


def _resolve_status(http_status: int | None, html: str) -> int:
    """Treat a post-challenge HTML body as success even when the first hop was 403."""
    if _looks_like_challenge_html(html):
        return http_status or 403
    if not (html or "").strip():
        return http_status or 403
    return 200


def _wait_past_challenge(page: Any, *, timeout_ms: int) -> None:
    deadline = time.monotonic() + min(timeout_ms / 1000.0, 45.0)
    while time.monotonic() < deadline:
        title = (page.title() or "").lower()
        html = page.content()
        if not _looks_like_challenge_html(html) and not any(m in title for m in _CHALLENGE_TITLE_MARKERS):
            return
        page.wait_for_timeout(2000)


class _PlaywrightSession:
    def __init__(self) -> None:
        self._playwright: Any = None
        self._browser: Any = None
        self._context: Any = None
        self._warmed_hosts: set[str] = set()

    def _ensure_started(self) -> None:
        if self._browser is not None:
            return
        from playwright.sync_api import sync_playwright

        self._playwright = sync_playwright().start()
        self._browser = self._playwright.chromium.launch(
            headless=True,
            args=list(_LAUNCH_ARGS),
        )
        self._context = self._browser.new_context(
            user_agent=_BROWSER_UA,
            locale="en-US",
            viewport={"width": 1440, "height": 900},
            extra_http_headers={
                "Accept-Language": "en-US,en;q=0.9",
                "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
            },
        )
        self._context.add_init_script(_STEALTH_INIT_SCRIPT)

    def _warm_host(self, url: str, *, timeout_ms: int = 45000) -> None:
        parsed = urlparse(url)
        host = (parsed.hostname or "").lower()
        if not host or host in self._warmed_hosts:
            return
        self._ensure_started()
        origin = f"{parsed.scheme or 'https'}://{host}/"
        page = self._context.new_page()
        try:
            page.goto(origin, wait_until="domcontentloaded", timeout=timeout_ms)
            _wait_past_challenge(page, timeout_ms=timeout_ms)
        finally:
            page.close()
        self._warmed_hosts.add(host)

    def _fetch_via_page(self, url: str, *, timeout_ms: int) -> tuple[int, bytes, str]:
        page = self._context.new_page()
        try:
            response = page.goto(url, wait_until="domcontentloaded", timeout=timeout_ms)
            _wait_past_challenge(page, timeout_ms=timeout_ms)
            html = page.content()
            http_status = response.status if response else 403
            status = _resolve_status(http_status, html)
            return status, html.encode("utf-8"), page.url
        finally:
            page.close()

    def _fetch_via_request(self, url: str, *, timeout_ms: int) -> tuple[int, bytes, str]:
        response = self._context.request.get(url, timeout=timeout_ms)
        body = response.body()
        if response.status < 400 or not _looks_like_challenge_html(
            body.decode("utf-8", errors="replace")
        ):
            return response.status, body, response.url
        return self._fetch_via_page(url, timeout_ms=timeout_ms)

    def fetch(self, url: str, *, timeout_ms: int = 45000) -> tuple[int, bytes, str]:
        self._ensure_started()
        self._warm_host(url, timeout_ms=timeout_ms)
        path = (urlparse(url).path or "").lower()
        # HTML pages and XML/text artifacts: full navigation handles Cloudflare challenges.
        if path.endswith((".png", ".jpg", ".jpeg", ".gif", ".webp", ".ico", ".avif", ".svg")):
            return self._fetch_via_request(url, timeout_ms=timeout_ms)
        return self._fetch_via_page(url, timeout_ms=timeout_ms)

    def close(self) -> None:
        if self._context is not None:
            self._context.close()
            self._context = None
        if self._browser is not None:
            self._browser.close()
            self._browser = None
        if self._playwright is not None:
            self._playwright.stop()
            self._playwright = None
        self._warmed_hosts.clear()


def _get_session() -> _PlaywrightSession:
    global _session
    if _session is None:
        _session = _PlaywrightSession()
    return _session


def fetch_with_browser_fallback(
    url: str,
    *,
    status: int | None,
    headers: Mapping[str, str] | None,
    body: bytes = b"",
) -> tuple[int, bytes, str] | None:
    """Retry ``url`` with Playwright when ``status`` looks like a bot wall. Returns None on skip/failure."""
    if not is_bot_wall(status, headers, body):
        return None
    with _lock:
        try:
            return _get_session().fetch(url)
        except Exception:
            return None


def fetch_once_with_browser(
    url: str,
    *,
    timeout_ms: int = 45000,
    max_attempts: int = 2,
) -> tuple[int, bytes, str]:
    """One-off browser fetch (wizard probe); does not reuse the shared crawl session."""
    last: tuple[int, bytes, str] = (403, b"", url)
    for attempt in range(max(1, max_attempts)):
        session = _PlaywrightSession()
        try:
            status, body, final_url = session.fetch(url, timeout_ms=timeout_ms)
            if status < 400:
                return status, body, final_url
            html = body.decode("utf-8", errors="replace")
            if not _looks_like_challenge_html(html):
                return status, body, final_url
            last = (status, body, final_url)
        except Exception as exc:
            import logging

            logging.getLogger(__name__).warning(
                "Playwright fetch attempt %s failed for %s: %s",
                attempt + 1,
                url,
                exc,
            )
        finally:
            session.close()
        if attempt + 1 < max_attempts:
            time.sleep(1.5)
    return last


def close_browser_session() -> None:
    global _session
    with _lock:
        if _session is not None:
            _session.close()
            _session = None
