"""Canonical GEO report score display + banding (mirrors web/src/lib/reportScore.ts).

Lives under ``backend/`` so ``create-report.py`` and other crawl-path modules can
import it with ``PYTHONPATH=backend`` (Cloud Run image default) without needing
the ``api`` package on the path.
"""

from __future__ import annotations

import math

GOOD_SCORE_MIN = 75


def round_report_score(score: float) -> int:
    """Nearest integer; matches JS Math.round (half away from zero for non-negative)."""
    x = float(score)
    if x >= 0:
        return int(math.floor(x + 0.5))
    return int(math.ceil(x - 0.5))


def format_report_score(score: float) -> str:
    """Display string for GEO / pillar / criterion scores (60.0 → '60', 60.9 → '61')."""
    try:
        x = float(score)
    except (TypeError, ValueError):
        return "—"
    if not math.isfinite(x):
        return "—"
    return str(round_report_score(x))


def score_label(score: float) -> str:
    """Verbal band — uses rounded display integer so 74.6 → Good."""
    s = round_report_score(score)
    if s >= 90:
        return "Excellent"
    if s >= GOOD_SCORE_MIN:
        return "Good"
    if s >= 60:
        return "OK"
    if s >= 40:
        return "Weak"
    return "Poor"


def score_tone(score: float) -> str:
    """Colour tone key — green/blue/yellow/red — banded on rounded display integer."""
    s = round_report_score(score)
    if s >= GOOD_SCORE_MIN:
        return "green"
    if s >= 60:
        return "blue"
    if s >= 40:
        return "yellow"
    return "red"


def is_ok_or_below(score: float) -> bool:
    """True when rounded display score is OK, Weak, or Poor."""
    try:
        return round_report_score(float(score)) < GOOD_SCORE_MIN
    except (TypeError, ValueError):
        return False
