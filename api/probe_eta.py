"""Rough wall-clock ETA for prompt probe runs.

Markets (locales) run in parallel, so ETA is driven by the slowest market's
platform-call count — never multiply by the number of markets.

Observed average ≈ 7 seconds per platform call (Cloud Run).
"""

from __future__ import annotations

import math

SECONDS_PER_PLATFORM_CALL = 7


def estimate_probe_run_seconds(platform_calls: int) -> int:
    """Wall-clock seconds for ``platform_calls`` (one market / max across markets)."""
    calls = max(0, int(platform_calls or 0))
    return calls * SECONDS_PER_PLATFORM_CALL


def estimate_probe_run_minutes(platform_calls: int) -> int:
    """Ceil minutes for ``platform_calls``. Returns 0 when there are no calls."""
    seconds = estimate_probe_run_seconds(platform_calls)
    if seconds <= 0:
        return 0
    return max(1, int(math.ceil(seconds / 60)))


def wall_clock_platform_calls(
    locale_entries: list[tuple[int, int]],
) -> tuple[int, int, int]:
    """Derive wall-clock planned/completed/remaining from per-locale (completed, planned).

    Returns ``(wall_planned, wall_completed, wall_remaining)`` where remaining is the
    max unfinished calls across locales (parallel markets).
    """
    wall_planned = 0
    wall_remaining = 0
    for completed, planned in locale_entries:
        p = max(0, int(planned or 0))
        c = max(0, min(p, int(completed or 0)))
        rem = max(0, p - c)
        if p > wall_planned:
            wall_planned = p
        if rem > wall_remaining:
            wall_remaining = rem
    wall_completed = max(0, wall_planned - wall_remaining) if wall_planned else 0
    return wall_planned, wall_completed, wall_remaining
