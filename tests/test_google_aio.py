from __future__ import annotations

import io
import urllib.error

import google_aio


def _not_found(model: str) -> urllib.error.HTTPError:
    return urllib.error.HTTPError(
        url=f"https://example.test/models/{model}",
        code=404,
        msg="Not Found",
        hdrs=None,
        fp=io.BytesIO(b'{"error":{"message":"model unavailable"}}'),
    )


def _successful_payload() -> dict:
    return {
        "candidates": [
            {
                "content": {
                    "parts": [
                        {"text": "Grounded answer"},
                    ]
                }
            }
        ]
    }


def test_grounded_models_default_to_current_model(monkeypatch) -> None:
    monkeypatch.setattr(google_aio, "_get_config", lambda _name: "")

    assert google_aio._grounded_models() == [
        "gemini-3.6-flash",
        "gemini-3.5-flash",
    ]
    assert google_aio._grounded_model() == "gemini-3.6-flash"


def test_grounded_models_prefer_configured_override(monkeypatch) -> None:
    monkeypatch.setattr(
        google_aio,
        "_get_config",
        lambda name: "custom-grounded-model" if name == "GEMINI_GROUNDED_MODEL" else "",
    )

    assert google_aio._grounded_models() == [
        "custom-grounded-model",
        "gemini-3.6-flash",
        "gemini-3.5-flash",
    ]


def test_grounded_answer_retries_next_model_after_404(monkeypatch) -> None:
    calls: list[str] = []
    monkeypatch.setattr(
        google_aio,
        "_get_config",
        lambda name: "retired-model" if name == "GEMINI_GROUNDED_MODEL" else "",
    )

    def request(**kwargs):
        model = kwargs["model"]
        calls.append(model)
        if model == "retired-model":
            raise _not_found(model)
        return _successful_payload()

    monkeypatch.setattr(google_aio, "_gemini_grounded_request", request)

    result = google_aio.gemini_grounded_answer("test", api_key="key")

    assert calls == ["retired-model", "gemini-3.6-flash"]
    assert result["response"] == "Grounded answer"
    assert result["error"] is None


def test_grounded_answer_returns_error_when_all_models_404(monkeypatch) -> None:
    calls: list[str] = []
    monkeypatch.setattr(google_aio, "_get_config", lambda _name: "")

    def request(**kwargs):
        model = kwargs["model"]
        calls.append(model)
        raise _not_found(model)

    monkeypatch.setattr(google_aio, "_gemini_grounded_request", request)

    result = google_aio.gemini_grounded_answer("test", api_key="key")

    assert calls == ["gemini-3.6-flash", "gemini-3.5-flash"]
    assert result["response"] == ""
    assert result["citations"] == []
    assert result["error"].startswith("Gemini HTTP 404:")
