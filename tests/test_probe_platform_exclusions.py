from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta

from api import probe_platforms


def test_stale_platform_exclusions_expire(monkeypatch, tmp_path) -> None:
    monkeypatch.setenv("GEO_DATA_ROOT", str(tmp_path))
    monkeypatch.setenv("PROMPT_PLATFORM_EXCLUSION_TTL_HOURS", "1")
    (tmp_path / "probe_excluded_platforms.json").write_text(
        json.dumps(
            {
                "excluded": {
                    "claude": {
                        "excluded_at": (datetime.now(UTC) - timedelta(hours=2)).isoformat(),
                        "reason": "old workspace usage limit",
                    }
                }
            }
        )
    )

    assert probe_platforms.get_excluded_platforms() == set()
    saved = json.loads((tmp_path / "probe_excluded_platforms.json").read_text())
    assert saved["excluded"] == {}


def test_recent_platform_exclusion_remains_active(monkeypatch, tmp_path) -> None:
    monkeypatch.setenv("GEO_DATA_ROOT", str(tmp_path))
    probe_platforms.exclude_platform("claude", "Anthropic HTTP 429: rate limit")

    assert probe_platforms.get_excluded_platforms() == {"claude"}


def test_generic_bad_request_does_not_disable_platform() -> None:
    assert not probe_platforms.is_fatal_platform_error(
        "claude",
        "Anthropic HTTP 400: invalid tool definition",
    )
    assert probe_platforms.is_fatal_platform_error(
        "claude",
        "Anthropic HTTP 400: workspace usage limit exceeded",
    )
