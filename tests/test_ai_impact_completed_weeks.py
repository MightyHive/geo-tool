"""Unit tests for Sunday-week completion filtering (AI Traffic Impact chart)."""

from datetime import date

from backend.ai_impact.panel import (
    filter_completed_weeks,
    is_completed_sunday_week,
    sunday_week_start,
)


def test_sunday_week_start() -> None:
    assert sunday_week_start(date(2026, 7, 23)) == date(2026, 7, 19)  # Thu → Sun
    assert sunday_week_start("2026-07-19") == date(2026, 7, 19)
    assert sunday_week_start("2026-07-25") == date(2026, 7, 19)  # Sat
    assert sunday_week_start("2026-07-26") == date(2026, 7, 26)  # next Sun


def test_is_completed_sunday_week_after_saturday() -> None:
    # Week of 2026-07-19 covers Sun Jul 19 – Sat Jul 25
    assert is_completed_sunday_week("2026-07-19", as_of=date(2026, 7, 25)) is False
    assert is_completed_sunday_week("2026-07-19", as_of=date(2026, 7, 26)) is True
    assert is_completed_sunday_week("2026-07-12", as_of=date(2026, 7, 23)) is True


def test_filter_completed_weeks_drops_in_progress() -> None:
    series = [
        {"week": "2026-07-05", "total_sessions": 100},
        {"week": "2026-07-12", "total_sessions": 110},
        {"week": "2026-07-19", "total_sessions": 50},  # in progress on Jul 23
    ]
    filtered = filter_completed_weeks(series, as_of=date(2026, 7, 23))
    assert [p["week"] for p in filtered] == ["2026-07-05", "2026-07-12"]
