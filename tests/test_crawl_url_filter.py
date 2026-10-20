from api.geo_services import filter_crawl_urls_for_site


def test_filter_crawl_urls_keeps_same_site_and_dedupes():
    urls = [
        "https://www.example.com/a",
        "example.com/b",
        "https://shop.example.com/c",
        "https://www.example.com/a",
        "",
    ]
    assert filter_crawl_urls_for_site(urls, "https://example.com") == [
        "https://www.example.com/a",
        "https://example.com/b",
        "https://shop.example.com/c",
    ]


def test_filter_crawl_urls_drops_invalid_and_off_domain():
    urls = ["https://other.com/x", "ftp://example.com/y", "not a url", "https://example.com/ok"]
    assert filter_crawl_urls_for_site(urls, "www.example.com") == ["https://example.com/ok"]
