from __future__ import annotations

import json
from types import SimpleNamespace

from fastapi import FastAPI
from fastapi.testclient import TestClient

from api import wizard
from api.geo_services import seed_audit_dir_from_wizard
from api.prompt_selection import MAX_PERSONA_PROMPTS_TOTAL, select_prompts_for_probing
from backend import geo_setup_llm


class _GeminiModels:
    def __init__(self, payload: str):
        self.payload = payload

    def generate_content(self, **_kwargs):
        return SimpleNamespace(text=self.payload)


def _stub_gemini(monkeypatch, payload) -> None:
    monkeypatch.setattr(
        geo_setup_llm,
        "build_genai_client",
        lambda: SimpleNamespace(models=_GeminiModels(json.dumps(payload))),
    )


def test_suggest_personas_dedupes_and_caps(monkeypatch) -> None:
    personas = [{"name": f"Persona {i}", "description": f"Desc {i}"} for i in range(6)]
    personas.insert(1, {"name": "persona 0", "description": "dupe"})
    _stub_gemini(monkeypatch, personas)

    out = geo_setup_llm.suggest_personas("https://example.com", brand_name="Example")

    assert [p["name"] for p in out] == [f"Persona {i}" for i in range(5)]
    assert out[0]["description"] == "Desc 0"


def test_suggest_persona_prompts_maps_topics_and_dedupes(monkeypatch) -> None:
    _stub_gemini(
        monkeypatch,
        [
            {
                "name": "Young families",
                "prompts": [
                    {"prompt": "Best family car insurance?", "product_or_service": "car insurance"},
                    {"prompt": "Cheap home cover for renters", "product_or_service": "Home"},
                    {"prompt": "Something else", "product_or_service": "Pets"},
                ],
            },
            {
                "name": "Retirees",
                "prompts": [{"prompt": "best family car insurance?", "product_or_service": "Car insurance"}],
            },
        ],
    )

    out = geo_setup_llm.suggest_prompts_for_personas(
        "https://example.com",
        [{"name": "Young families"}, {"name": "Retirees"}],
        ["Car insurance", "Home insurance"],
    )

    assert len(out) == 1  # Retirees' only prompt was a duplicate
    topics = [p["product_or_service"] for p in out[0]["prompts"]]
    assert topics == ["Car insurance", "Home insurance", "Car insurance"]


def test_persona_endpoints(monkeypatch) -> None:
    app = FastAPI()
    app.include_router(wizard.router)
    client = TestClient(app)
    import geo_setup_llm as endpoint_llm  # api.wizard imports the module via backend/ on sys.path

    monkeypatch.setattr(
        endpoint_llm,
        "suggest_personas",
        lambda url, **kw: [{"name": "Savers", "description": "Want value"}],
    )
    res = client.post("/api/wizard/suggest-personas", json={"brand_website": "example.com"})
    assert res.status_code == 200
    assert res.json()["personas"] == [{"name": "Savers", "description": "Want value"}]

    assert client.post("/api/wizard/suggest-personas", json={"brand_website": ""}).status_code == 400
    bad = client.post(
        "/api/wizard/suggest-persona-prompts",
        json={"brand_website": "example.com", "personas": [], "products": ["A"]},
    )
    assert bad.status_code == 422


def test_persona_prompts_use_separate_budget_round_robin() -> None:
    rows = [
        {
            "product_or_service": f"Topic {t}",
            "prompts": [f"t{t}-{i}" for i in range(5)] + [f"t{t}-persona-{p}-{i}" for p in range(5) for i in range(2)],
            "prompt_personas": {f"t{t}-persona-{p}-{i}": f"P{p}" for p in range(5) for i in range(2)},
        }
        for t in range(5)
    ]

    flat, selected = select_prompts_for_probing(rows)

    persona_prompts = [p for p in flat if "persona" in p]
    topic_prompts = [p for p in flat if "persona" not in p]
    assert len(topic_prompts) == 25
    assert len(persona_prompts) == MAX_PERSONA_PROMPTS_TOTAL
    per_persona = {f"P{p}": sum(1 for x in persona_prompts if f"persona-{p}-" in x) for p in range(5)}
    assert set(per_persona.values()) == {5}
    # Persona prompts merged into topic rows, no duplicate rows.
    labels = [r["product_or_service"] for r in selected]
    assert labels == [f"Topic {t}" for t in range(5)]
    first = selected[0]
    assert any("persona" in p for p in first["prompts"])
    assert all(v.startswith("P") for v in first["prompt_personas"].values())


def test_persona_only_topic_row_is_selected() -> None:
    rows = [
        {"product_or_service": "A", "prompts": ["a1"]},
        {"product_or_service": "B", "prompts": ["b-persona"], "prompt_personas": {"b-persona": "Savers"}},
    ]
    flat, selected = select_prompts_for_probing(rows)
    assert flat == ["a1", "b-persona"]
    assert selected[1]["prompt_personas"] == {"b-persona": "Savers"}


def test_seed_persists_personas_and_prompt_personas(tmp_path) -> None:
    seed_audit_dir_from_wizard(
        tmp_path,
        primary_url="https://example.com",
        brand_name="Example",
        industry="Insurance",
        market_country="United Kingdom",
        market_country_code="GB",
        competitor_urls=[],
        products_rows=[
            {
                "product_or_service": "Car insurance",
                "prompts": ["q1", "q2"],
                "prompt_personas": {"q2": "Young drivers", "ghost": "X"},
            }
        ],
        competitors_detail=[],
        personas=[{"name": "Young drivers", "description": "Under 25"}, {"name": " "}],
    )
    pss = json.loads((tmp_path / "products_and_services.json").read_text())
    assert pss["rows"][0]["prompt_personas"] == {"q2": "Young drivers"}
    ob = json.loads((tmp_path / "onboarding_context.json").read_text())
    assert ob["personas"] == [{"name": "Young drivers", "description": "Under 25"}]
