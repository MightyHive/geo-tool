"""Re-export score helpers from ``backend/report_score.py`` for API callers.

Implementation lives under ``backend/`` so crawl/create-report never depends on
``api.*`` being importable (Cloud Run sets ``PYTHONPATH=/app/backend``).
"""

from __future__ import annotations

from report_score import (
    GOOD_SCORE_MIN,
    format_report_score,
    is_ok_or_below,
    round_report_score,
    score_label,
    score_tone,
)

__all__ = [
    "GOOD_SCORE_MIN",
    "format_report_score",
    "is_ok_or_below",
    "round_report_score",
    "score_label",
    "score_tone",
]
