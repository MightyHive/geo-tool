from __future__ import annotations

from pathlib import Path

from api.prompt_performance import read_latest_probe_progress, summarize_probe_progress_event


def test_summarize_probe_progress_parallel_markets_not_multiplied() -> None:
    summary = summarize_probe_progress_event(
        {
            "status": "complete",
            "locale_index": 1,
            "locale_total": 2,
            "completed_calls": 10,
            "planned_calls": 40,
            "prompt_index": 3,
            "prompt_total": 10,
            "job_elapsed_seconds": 100,
            "locale_label": "United Kingdom · English",
        }
    )
    assert summary["market_count"] == 2
    # Parallel markets: do not multiply planned by locale_total.
    assert summary["completed_calls"] == 10
    assert summary["planned_calls"] == 40
    assert summary["prompt_index"] == 3
    assert summary["prompt_total"] == 10
    # Remaining 30 calls × 7s = 210s
    assert summary["eta_seconds"] == 210
    assert summary["eta_total_seconds"] == 280


def test_summarize_probe_progress_second_market_stays_per_locale() -> None:
    summary = summarize_probe_progress_event(
        {
            "status": "complete",
            "locale_index": 2,
            "locale_total": 2,
            "completed_calls": 5,
            "planned_calls": 40,
            "job_elapsed_seconds": 200,
        }
    )
    assert summary["completed_calls"] == 5
    assert summary["planned_calls"] == 40
    assert summary["eta_seconds"] == 35 * 7


def test_read_latest_probe_progress(tmp_path: Path) -> None:
    path = tmp_path / "prompt_probe_progress.jsonl"
    path.write_text(
        "\n".join(
            [
                '{"status":"started","locale_index":1,"locale_total":1,"completed_calls":0,"planned_calls":4,"prompt_total":1}',
                '{"status":"complete","locale_index":1,"locale_total":1,"completed_calls":2,"planned_calls":4,"prompt_index":1,"prompt_total":1,"job_elapsed_seconds":20}',
            ]
        )
        + "\n",
        encoding="utf-8",
    )
    latest = read_latest_probe_progress(tmp_path)
    assert latest is not None
    assert latest["completed_calls"] == 2
    assert latest["planned_calls"] == 4
    assert latest["market_count"] == 1
    # Remaining 2 calls × 7s
    assert latest["eta_seconds"] == 14
    assert latest["eta_total_seconds"] == 28
