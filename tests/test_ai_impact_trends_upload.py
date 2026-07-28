from __future__ import annotations

import asyncio
import io

import numpy as np
import pandas as pd
import pytest
from starlette.datastructures import UploadFile

from api.ai_impact import upload_trends_csv
from backend.ai_impact.estimate import _detrend_beta
from backend.ai_impact.panel import build_weekly_panel
from backend.ai_impact.trends import TrendsUploadError, parse_google_trends_upload


def _weekly_csv(start: str = "2023-01-01", periods: int = 6) -> bytes:
    dates = pd.date_range(start, periods=periods, freq="7D")
    rows = [
        "Category: All categories",
        "",
        "Week,home improvement: (United Kingdom),DIY: (United Kingdom)",
    ]
    rows.extend(
        f"{day.date().isoformat()},{20 + index},{'<1' if index == 0 else 10 + index}"
        for index, day in enumerate(dates)
    )
    return ("\n".join(rows) + "\n").encode()


def test_manual_trends_upload_parses_google_export() -> None:
    parsed = parse_google_trends_upload(
        _weekly_csv(),
        expected_start="2023-01-01",
        expected_end="2023-02-12",
    )

    assert parsed.terms == ["home improvement", "DIY"]
    assert parsed.week_count == 6
    assert parsed.weekly.columns.tolist() == ["week", "trends_1", "trends_2"]
    assert parsed.weekly.loc[0, "trends_2"] == 0.5


def test_manual_trends_upload_rejects_wrong_grain() -> None:
    content = b"Day,DIY: (United Kingdom)\n2023-01-01,20\n2023-01-02,21\n"

    with pytest.raises(TrendsUploadError, match="weekly rows are required"):
        parse_google_trends_upload(
            content,
            expected_start="2023-01-01",
            expected_end="2023-01-02",
        )


def test_manual_trends_upload_reports_missing_date_coverage() -> None:
    with pytest.raises(TrendsUploadError, match="must cover 2023-01-01"):
        parse_google_trends_upload(
            _weekly_csv(start="2023-03-05"),
            expected_start="2023-01-01",
            expected_end="2023-04-09",
        )


def test_upload_endpoint_persists_validated_file(monkeypatch, tmp_path) -> None:
    monkeypatch.setenv("GEO_DATA_ROOT", str(tmp_path))
    upload = UploadFile(file=io.BytesIO(_weekly_csv()), filename="multiTimeline.csv")

    result = asyncio.run(
        upload_trends_csv(
            file=upload,
            start_date="2023-01-01",
            end_date="2023-02-12",
        )
    )

    assert result.filename == "multiTimeline.csv"
    assert result.terms == ["home improvement", "DIY"]
    assert (tmp_path / "ai_impact_trends_uploads" / f"{result.upload_id}.csv").is_file()


def test_weekly_panel_merges_manual_trends_controls() -> None:
    weeks = pd.date_range("2023-01-01", periods=6, freq="7D")
    ga4 = pd.DataFrame(
        {
            "week": weeks,
            "sessions": [100] * 6,
            "purchases": [5] * 6,
            "ai_sessions": range(6),
            "seo_sessions": [50] * 6,
            "other_traffic_sessions": [50] * 6,
        }
    )
    trends = pd.DataFrame({"week": weeks, "trends_1": range(10, 16)})

    panel = build_weekly_panel(ga4, trends=trends)

    assert panel["trends_1"].tolist() == list(range(10, 16))


def test_trends_control_removes_shared_demand_signal() -> None:
    rng = np.random.default_rng(7)
    demand = np.linspace(10, 100, 80) + rng.normal(0, 2, 80)
    ai_sessions = demand + rng.normal(0, 2, 80)
    outcome = 3 * demand + rng.normal(0, 2, 80)

    uncontrolled = _detrend_beta(outcome, ai_sessions)
    controlled = _detrend_beta(outcome, ai_sessions, demand)

    assert abs(controlled) < abs(uncontrolled)
