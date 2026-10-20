from api.prompt_performance import bootstrap_locale_key


def _locales(n: int) -> list[dict]:
    return [{"key": f"C{i}:en"} for i in range(n)]


def test_single_locale_audits_load_all_locales() -> None:
    assert bootstrap_locale_key({"prompt_locales": _locales(1), "prompt_count": 500}) is None
    assert bootstrap_locale_key(None) is None


def test_small_two_locale_audit_loads_all_locales() -> None:
    assert bootstrap_locale_key({"prompt_locales": _locales(2), "prompt_count": 10}) is None


def test_huge_audit_opens_on_default_locale() -> None:
    metrics = {"prompt_locales": _locales(3), "prompt_count": 10, "default_locale_key": "C1:en"}
    assert bootstrap_locale_key(metrics) == "C1:en"


def test_huge_audit_falls_back_to_first_locale() -> None:
    metrics = {"prompt_locales": _locales(2), "prompt_count": 40, "default_locale_key": "XX:en"}
    assert bootstrap_locale_key(metrics) == "C0:en"
