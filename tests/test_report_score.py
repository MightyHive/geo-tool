"""Unit tests for canonical report score display + banding."""

from __future__ import annotations

import ast
import importlib
import os
import subprocess
import sys
from pathlib import Path

from report_score import (
    format_report_score,
    is_ok_or_below,
    round_report_score,
    score_label,
    score_tone,
)

REPO_ROOT = Path(__file__).resolve().parents[1]
BACKEND_ROOT = REPO_ROOT / "backend"


def test_round_and_format_nearest_integer() -> None:
    assert round_report_score(60.0) == 60
    assert round_report_score(60.9) == 61
    assert round_report_score(60.5) == 61
    assert format_report_score(60.0) == "60"
    assert format_report_score(60.9) == "61"
    assert format_report_score(72.5) == "73"


def test_band_on_rounded_display_integer() -> None:
    assert score_label(74.6) == "Good"
    assert score_tone(74.6) == "green"
    assert is_ok_or_below(74.6) is False
    assert score_label(74.4) == "OK"
    assert score_tone(74.4) == "blue"
    assert is_ok_or_below(74.4) is True


def test_api_report_score_reexports_backend() -> None:
    """API package may re-export; keep behaviour identical to backend module."""
    repo_root = str(REPO_ROOT)
    if repo_root not in sys.path:
        sys.path.insert(0, repo_root)
    api_mod = importlib.import_module("api.report_score")
    assert api_mod.format_report_score(60.9) == format_report_score(60.9)
    assert api_mod.score_label(74.6) == score_label(74.6)
    assert api_mod.score_tone(74.4) == score_tone(74.4)


def test_create_report_score_import_with_backend_only_on_path() -> None:
    """Crawl/Cloud Run sets PYTHONPATH=backend; create-report must not need api.*."""
    create_report = BACKEND_ROOT / "create-report.py"
    tree = ast.parse(create_report.read_text(encoding="utf-8"))
    forbidden: list[str] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom) and node.module and node.module.startswith("api"):
            forbidden.append(f"from {node.module}")
        elif isinstance(node, ast.Import):
            for alias in node.names:
                if alias.name == "api" or alias.name.startswith("api."):
                    forbidden.append(f"import {alias.name}")
    assert not forbidden, f"create-report.py must not import api.* at module level: {forbidden}"

    smoke = (
        "from report_score import format_report_score, score_label, score_tone; "
        "assert format_report_score(60.9) == '61'; "
        "assert score_label(74.6) == 'Good'; "
        "assert score_tone(40) == 'yellow'"
    )
    env = os.environ.copy()
    env["PYTHONPATH"] = str(BACKEND_ROOT)
    proc = subprocess.run(
        [sys.executable, "-c", smoke],
        cwd=str(REPO_ROOT),
        env=env,
        capture_output=True,
        text=True,
        check=False,
    )
    assert proc.returncode == 0, proc.stderr or proc.stdout
