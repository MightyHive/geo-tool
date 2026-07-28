from __future__ import annotations

import json
from types import SimpleNamespace
from unittest.mock import MagicMock

from api import geo_services


def test_competitor_urls_for_crawl_dedupes_and_limits(tmp_path) -> None:
    (tmp_path / "onboarding_context.json").write_text(
        json.dumps({
            "competitors_detail": [
                {"competitor_brand": "A", "competitor_website": "https://a.example/"},
                {"competitor_brand": "B", "competitor_website": "https://b.example"},
            ],
            "accepted_competitors": [
                "https://a.example",
                "https://c.example",
            ],
        }),
        encoding="utf-8",
    )

    urls = geo_services.competitor_urls_for_crawl(tmp_path)
    assert urls == [
        "https://a.example/",
        "https://b.example",
        "https://c.example",
    ]


def test_archive_current_competitor_crawl_snapshots_comparison(tmp_path) -> None:
    (tmp_path / "comparison.json").write_text('{"rows":[]}', encoding="utf-8")
    (tmp_path / "comparison.md").write_text("# cmp\n", encoding="utf-8")
    competitors = tmp_path / "competitors" / "peer_example"
    competitors.mkdir(parents=True)
    (competitors / "audit_summary.json").write_text("{}", encoding="utf-8")
    geo_services.write_competitor_crawl_status(
        tmp_path,
        {
            "status": "done",
            "finished_at": "2026-01-01T00:00:00+00:00",
            "crawled": ["https://peer.example"],
            "competitor_count": 1,
            "seen": True,
        },
    )

    archive_id = geo_services.archive_current_competitor_crawl(tmp_path)
    assert archive_id
    dest = tmp_path / "competitor_crawls" / archive_id
    assert (dest / "comparison.json").is_file()
    assert (dest / "competitors" / "peer_example" / "audit_summary.json").is_file()
    assert (dest / "manifest.json").is_file()


def test_crawl_competitors_in_place_writes_comparison(tmp_path, monkeypatch) -> None:
    (tmp_path / "audit_summary.json").write_text(
        json.dumps({"base_url": "https://brand.example", "pages": []}),
        encoding="utf-8",
    )
    (tmp_path / "comparison.json").write_text('{"rows":[{"site":"old"}]}', encoding="utf-8")
    (tmp_path / "onboarding_context.json").write_text(
        json.dumps({
            "brand_name_used": "Brand",
            "competitors_detail": [
                {"competitor_brand": "Peer", "competitor_website": "https://peer.example"},
            ],
        }),
        encoding="utf-8",
    )

    fake_crawl = SimpleNamespace(
        configure_tls=lambda **_kwargs: {"mode": "test"},
        normalize_base=lambda url: url.rstrip("/"),
        safe_dir_name=lambda url: url.replace("https://", "").replace(".", "_"),
        run_site_audit=MagicMock(
            return_value={
                "base_url": "https://peer.example",
                "output_dir": str(tmp_path / "competitors" / "peer_example"),
                "pages": [],
                "summary": {},
                "robots_txt": {"exists": True},
                "llms_txt": {"exists": False},
            }
        ),
        write_comparison_files=MagicMock(
            return_value=(
                str(tmp_path / "comparison.json"),
                str(tmp_path / "comparison.md"),
            )
        ),
    )
    monkeypatch.setattr(geo_services, "load_crawl_site", lambda: fake_crawl)

    fake_create = SimpleNamespace(generate_reports=MagicMock(return_value=0))
    monkeypatch.setattr(geo_services, "load_create_report", lambda: fake_create)

    result = geo_services.crawl_competitors_in_place(tmp_path)

    assert result["competitor_count"] == 1
    assert result["crawled"] == ["https://peer.example"]
    assert result["archived_id"]
    assert (tmp_path / "competitor_crawls" / result["archived_id"] / "comparison.json").is_file()
    fake_crawl.run_site_audit.assert_called_once()
    fake_crawl.write_comparison_files.assert_called_once()
    fake_create.generate_reports.assert_called_once()
    saved = json.loads((tmp_path / "onboarding_context.json").read_text(encoding="utf-8"))
    assert "https://peer.example" in saved["accepted_competitors"]


def test_mark_competitor_crawl_seen(tmp_path) -> None:
    geo_services.write_competitor_crawl_status(
        tmp_path,
        {"status": "done", "seen": False, "competitor_count": 2},
    )
    result = geo_services.mark_competitor_crawl_seen(tmp_path)
    assert result["seen"] is True
    assert geo_services.read_competitor_crawl_status(tmp_path)["seen"] is True


def test_reconcile_competitor_crawl_status_marks_stale_running(tmp_path, monkeypatch) -> None:
    monkeypatch.setattr(
        geo_services,
        "running_status_age_seconds",
        lambda _payload: 20 * 3600.0,
    )
    geo_services.write_competitor_crawl_status(
        tmp_path,
        {
            "status": "running",
            "started_at": "2026-07-21T14:00:00+00:00",
            "percent": 26,
            "detail": "Crawling competitor 2/4",
            "seen": False,
        },
    )

    reconciled = geo_services.reconcile_competitor_crawl_status(tmp_path)
    assert reconciled is not None
    assert reconciled["status"] == "error"
    assert reconciled.get("stale") is True
    assert "retry" in str(reconciled.get("detail") or "").lower()


def test_reconcile_competitor_crawl_status_keeps_fresh_running(tmp_path, monkeypatch) -> None:
    monkeypatch.setattr(geo_services, "running_status_age_seconds", lambda _payload: 120.0)
    geo_services.write_competitor_crawl_status(
        tmp_path,
        {
            "status": "running",
            "started_at": "2026-07-22T11:58:00+00:00",
            "percent": 10,
            "detail": "Crawling…",
            "seen": False,
        },
    )
    (tmp_path / "competitor_crawl_pending.json").write_text("{}", encoding="utf-8")
    reconciled = geo_services.reconcile_competitor_crawl_status(tmp_path)
    assert reconciled is not None
    assert reconciled["status"] == "running"


def test_reconcile_audit_run_status_clears_when_report_exists(tmp_path) -> None:
    from api import audit_runner

    (tmp_path / "report.html").write_text("<html></html>", encoding="utf-8")
    (tmp_path / "audit_summary.json").write_text(
        json.dumps({"overall_score": 71}),
        encoding="utf-8",
    )
    (tmp_path / "audit_run_status.json").write_text(
        json.dumps(
            {
                "status": "running",
                "percent": 62,
                "detail": "Prompt probes…",
                "updated_at": "2026-07-17T15:17:39+00:00",
            }
        ),
        encoding="utf-8",
    )

    reconciled = audit_runner.reconcile_audit_run_status(tmp_path)
    assert reconciled is not None
    assert reconciled["status"] == "done"
    assert reconciled.get("stale_cleared") is True
    assert json.loads((tmp_path / "audit_run_status.json").read_text())["status"] == "done"


def test_queue_follow_on_competitor_crawl_noop_without_competitors(tmp_path, monkeypatch) -> None:
    from api import audit_runner

    called = []
    monkeypatch.setattr(
        "api.crawl_jobs.crawl_jobs_enabled",
        lambda: True,
    )
    monkeypatch.setattr(
        "api.crawl_jobs.enqueue_crawl_job",
        lambda *a, **k: called.append(True) or {},
    )
    audit_runner._queue_follow_on_competitor_crawl(tmp_path, [])
    assert called == []


def test_queue_follow_on_competitor_crawl_enqueues_when_configured(tmp_path, monkeypatch) -> None:
    from api import audit_runner

    called = []
    monkeypatch.setattr(
        "api.crawl_jobs.crawl_jobs_enabled",
        lambda: True,
    )
    monkeypatch.setattr(
        "api.crawl_jobs.enqueue_crawl_job",
        lambda audit_dir, mode="competitors_only", payload=None: called.append(
            (str(audit_dir), mode)
        )
        or {},
    )
    audit_runner._queue_follow_on_competitor_crawl(tmp_path, ["https://rival.com"])
    assert called == [(str(tmp_path), "competitors_only")]
