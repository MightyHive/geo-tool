from __future__ import annotations

from types import SimpleNamespace

import browser_fetch

from api import geo_services


def test_transport_recovery_promotes_host_to_browser_first(monkeypatch) -> None:
    crawl = geo_services.load_crawl_site()
    crawl._BROWSER_FIRST_HOSTS.clear()
    urllib_calls: list[str] = []
    browser_calls: list[dict] = []

    def timed_out(url: str, *, user_agent: str):
        urllib_calls.append(url)
        return crawl.FetchResult(
            url=url,
            status=None,
            error="The read operation timed out",
        )

    def browser_success(url: str, **kwargs):
        browser_calls.append(kwargs)
        return 200, b"<html>BMW</html>", url

    monkeypatch.setattr(crawl, "_request_urllib", timed_out)
    monkeypatch.setattr(browser_fetch, "fetch_with_browser_fallback", browser_success)

    first = crawl._request("https://www.bmw.co.uk/en/index.html")
    second = crawl._request("https://www.bmw.co.uk/en/configurator.html")

    assert first.status == 200
    assert second.status == 200
    assert urllib_calls == ["https://www.bmw.co.uk/en/index.html"]
    assert browser_calls[0]["error"] == "The read operation timed out"
    assert browser_calls[1]["force"] is True


def test_ai_bot_mimic_does_not_use_browser_fallback(monkeypatch) -> None:
    crawl = geo_services.load_crawl_site()
    crawl._BROWSER_FIRST_HOSTS.add("www.bmw.co.uk")
    called = False

    def timed_out(url: str, *, user_agent: str):
        return crawl.FetchResult(url=url, status=None, error="timed out")

    def browser_success(*_args, **_kwargs):
        nonlocal called
        called = True
        return 200, b"", "https://www.bmw.co.uk/"

    monkeypatch.setattr(crawl, "_request_urllib", timed_out)
    monkeypatch.setattr(browser_fetch, "fetch_with_browser_fallback", browser_success)

    result = crawl._request(
        "https://www.bmw.co.uk/",
        user_agent="GoogleOther",
        allow_browser_fallback=False,
    )

    assert result.status is None
    assert called is False


def test_competitor_args_clear_primary_include_urls() -> None:
    crawl = geo_services.load_crawl_site()
    primary_args = SimpleNamespace(
        include_urls='["https://www.bmw.co.uk/en/index.html"]',
        delay=0.2,
    )

    competitor_args = crawl.competitor_crawl_args(primary_args)

    assert competitor_args.include_urls == ""
    assert primary_args.include_urls == '["https://www.bmw.co.uk/en/index.html"]'
    assert competitor_args.delay == 0.2


def test_explicit_target_audit_dir_overrides_hash_folder(tmp_path) -> None:
    crawl = geo_services.load_crawl_site()
    target = tmp_path / "www.bmw.co.uk_run_20260924"
    args = SimpleNamespace(out=tmp_path / "audit_output", target_audit_dir=target)

    resolved = crawl.primary_output_dir(args, "https://www.bmw.co.uk")

    assert resolved == str(target.resolve())


def test_create_report_command_carries_exact_target_directory(tmp_path) -> None:
    target = tmp_path / "audit_output" / "www.bmw.co.uk_run_20260924"

    command, _env = geo_services.create_report_cmd_env(
        "https://www.bmw.co.uk/en/index.html",
        [],
        "audit_output",
        40,
        0.2,
        target_audit_dir=target,
    )

    flag_index = command.index("--target-audit-dir")
    assert command[flag_index + 1] == str(target.resolve())
