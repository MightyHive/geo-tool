from __future__ import annotations

import os
import sys
from types import SimpleNamespace

from api.ai_impact import _run_ga4_channel_export


def test_ga4_export_restores_service_account_adc(tmp_path, monkeypatch) -> None:
    user_adc = tmp_path / "ga4_adc.json"
    user_adc.write_text("{}")
    weekly = tmp_path / "source_weekly.csv"
    weekly.write_text("week,sessions\n2026-01-01,1\n")
    captured: dict[str, object] = {}

    def fake_export(*_args, **kwargs):
        captured.update(kwargs)
        os.environ["GOOGLE_APPLICATION_CREDENTIALS"] = str(user_adc)
        return tmp_path / "long.csv", tmp_path / "wide.csv", weekly

    monkeypatch.delenv("GOOGLE_APPLICATION_CREDENTIALS", raising=False)
    monkeypatch.setitem(sys.modules, "ga4_channel_export", SimpleNamespace(run_export=fake_export))

    result = _run_ga4_channel_export(
        property_id="123",
        property_name="Example",
        start_date="2026-01-01",
        end_date="2026-02-01",
        run_dir=tmp_path,
        adc_path=user_adc,
        conversion_event_name="generate_lead",
    )

    assert result == tmp_path / "ga4_weekly.csv"
    assert captured["conversion_event_name"] == "generate_lead"
    assert "GOOGLE_APPLICATION_CREDENTIALS" not in os.environ
