from __future__ import annotations

from types import SimpleNamespace

from api.geo_services import load_create_report, load_crawl_site


def test_invalid_fetch_url_returns_error_instead_of_raising() -> None:
    crawl = load_crawl_site()

    result = crawl._request_urllib("/sitemap.xml")

    assert result.status is None
    assert "unknown url type" in (result.error or "")


def test_crawler_resolves_relative_robots_nested_and_page_urls(monkeypatch) -> None:
    crawl = load_crawl_site()
    requested: list[str] = []

    sitemap_index = b"""\
        <sitemapindex xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">
          <sitemap><loc>/nested/sitemap.xml</loc></sitemap>
          <sitemap><loc>javascript:invalid</loc></sitemap>
        </sitemapindex>
    """
    page_sitemap = b"""\
        <urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">
          <url><loc>../products/example</loc></url>
          <url><loc>data:text/plain,invalid</loc></url>
        </urlset>
    """

    def fake_request(url: str):
        requested.append(url)
        if url == "https://example.com/sitemap.xml":
            return crawl.FetchResult(url=url, status=200, body=sitemap_index)
        if url == "https://example.com/nested/sitemap.xml":
            return crawl.FetchResult(url=url, status=200, body=page_sitemap)
        return crawl.FetchResult(url=url, status=404)

    monkeypatch.setattr(crawl, "_request", fake_request)

    urls = crawl.collect_sitemap_page_urls(
        "https://example.com",
        "Sitemap: /sitemap.xml",
        max_sitemaps=5,
        max_urls=10,
    )

    assert urls == ["https://example.com/products/example"]
    assert requested
    assert all(url.startswith(("http://", "https://")) for url in requested)


def test_report_resolves_relative_sitemap_url(monkeypatch) -> None:
    report = load_create_report()
    requested: list[str] = []
    monkeypatch.setattr(
        report,
        "_fetch_http_status",
        lambda url: requested.append(url) or 200,
    )
    robots = SimpleNamespace(can_fetch=lambda _agent, _url: True)

    score, notes = report._sitemap_access_points(
        "Sitemap: /sitemap.xml",
        "https://example.com/",
        robots,
    )

    assert score == 3.0
    assert requested == ["https://example.com/sitemap.xml"]
    assert any("reachable" in note for note in notes)
