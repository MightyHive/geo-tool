from __future__ import annotations

def test_live_probe_reports_each_platform_call(monkeypatch) -> None:
    import sys
    import types

    import prompt_suggest as probe
    import api.probe_platforms as platform_state
    geo_setup_llm = types.ModuleType("geo_setup_llm")
    geo_setup_llm.suggest_reply_detected_competitor_brands = lambda *_args, **_kwargs: []
    monkeypatch.setitem(sys.modules, "geo_setup_llm", geo_setup_llm)

    monkeypatch.setattr(probe, "_openai_api_key", lambda: "openai-key")
    monkeypatch.setattr(probe, "_gemini_api_key", lambda: "gemini-key")
    monkeypatch.setattr(probe, "_anthropic_api_key", lambda: "claude-key")
    monkeypatch.setattr(platform_state, "get_excluded_platforms", lambda: set())
    monkeypatch.setattr(platform_state, "exclude_platform", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(platform_state, "is_fatal_platform_error", lambda *_args, **_kwargs: False)
    monkeypatch.setattr(
        probe,
        "gemini_answer_with_citations",
        lambda *_args, **_kwargs: ("Gemini answer", []),
    )
    monkeypatch.setattr(
        probe,
        "openai_answer_with_citations",
        lambda *_args, **_kwargs: ("OpenAI answer", []),
    )
    monkeypatch.setattr(
        probe,
        "claude_answer_user_prompt",
        lambda *_args, **_kwargs: "Claude answer",
    )
    monkeypatch.setattr(
        probe,
        "_aio_grounded_answer",
        lambda *_args, **_kwargs: {"response": "AIO answer", "citations": []},
    )
    events: list[dict] = []

    result = probe.run_live_prompt_probes(
        ["Which product is best?"],
        brand_name="Example",
        brand_site_url="https://example.com",
        competitor_urls=[],
        num_runs=1,
        max_prompts=1,
        progress_callback=events.append,
    )

    assert len(result["per_prompt"]) == 1
    assert events[0]["status"] == "started"
    assert events[0]["planned_calls"] == 4
    completed = [event for event in events if event["status"] == "complete"]
    assert {event["platform"] for event in completed} == {
        "gemini",
        "openai",
        "claude",
        "google_aio",
    }
    assert completed[-1]["completed_calls"] == 4
    assert result["per_prompt"][0]["gemini_response"]
    assert result["per_prompt"][0]["openai_response"]
    assert result["per_prompt"][0]["claude_response"]
    assert result["per_prompt"][0]["google_aio_response"]
