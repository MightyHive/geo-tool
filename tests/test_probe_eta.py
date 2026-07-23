from __future__ import annotations

from api.probe_eta import (
    SECONDS_PER_PLATFORM_CALL,
    estimate_probe_run_minutes,
    estimate_probe_run_seconds,
    wall_clock_platform_calls,
)


def test_seconds_per_platform_call_constant() -> None:
    assert SECONDS_PER_PLATFORM_CALL == 7


def test_estimate_minutes_100_calls_is_12() -> None:
    # 100 platform calls × 7s / 60 ≈ 11.67 → ceil 12; markets do not multiply.
    assert estimate_probe_run_minutes(100) == 12
    assert estimate_probe_run_seconds(100) == 700


def test_estimate_minutes_zero_and_ceil() -> None:
    assert estimate_probe_run_minutes(0) == 0
    assert estimate_probe_run_minutes(1) == 1
    assert estimate_probe_run_minutes(9) == 2


def test_wall_clock_uses_max_remaining_across_locales() -> None:
    planned, completed, remaining = wall_clock_platform_calls(
        [
            (90, 100),  # almost done
            (10, 100),  # slowest
            (50, 80),
        ]
    )
    assert planned == 100
    assert remaining == 90
    assert completed == 10
