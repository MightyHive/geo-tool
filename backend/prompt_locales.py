"""Multi-market / multi-language prompt probe locales.

Each locale is a market+language pair (e.g. United Kingdom + English → ``GB:en``).
Historical audits without ``prompt_locales`` synthesize a single default:
primary market (``geo_market_*``) + English.
"""

from __future__ import annotations

import json
import logging
import re
from typing import Any

from geo_market import resolve_primary_market

log = logging.getLogger(__name__)

MAX_PROMPT_LOCALES = 12

# ISO 639-1 → display name (subset used in the wizard / Config UI).
SUPPORTED_LANGUAGES: list[tuple[str, str]] = [
    ("en", "English"),
    ("fr", "French"),
    ("de", "German"),
    ("es", "Spanish"),
    ("it", "Italian"),
    ("nl", "Dutch"),
    ("pt", "Portuguese"),
    ("pl", "Polish"),
    ("sv", "Swedish"),
    ("da", "Danish"),
    ("no", "Norwegian"),
    ("fi", "Finnish"),
    ("cs", "Czech"),
    ("hu", "Hungarian"),
    ("ro", "Romanian"),
    ("el", "Greek"),
    ("tr", "Turkish"),
    ("ar", "Arabic"),
    ("he", "Hebrew"),
    ("ja", "Japanese"),
    ("ko", "Korean"),
    ("zh", "Chinese"),
]

_LANGUAGE_NAMES = {code: name for code, name in SUPPORTED_LANGUAGES}

# Common official / primary language for ISO2 country codes (fallback: English).
_COUNTRY_DEFAULT_LANGUAGE: dict[str, str] = {
    "GB": "en",
    "US": "en",
    "IE": "en",
    "AU": "en",
    "NZ": "en",
    "CA": "en",
    "FR": "fr",
    "BE": "fr",
    "CH": "de",
    "DE": "de",
    "AT": "de",
    "ES": "es",
    "MX": "es",
    "AR": "es",
    "CL": "es",
    "CO": "es",
    "IT": "it",
    "NL": "nl",
    "PT": "pt",
    "BR": "pt",
    "PL": "pl",
    "SE": "sv",
    "DK": "da",
    "NO": "no",
    "FI": "fi",
    "CZ": "cs",
    "HU": "hu",
    "RO": "ro",
    "GR": "el",
    "TR": "tr",
    "SA": "ar",
    "AE": "ar",
    "IL": "he",
    "JP": "ja",
    "KR": "ko",
    "CN": "zh",
    "TW": "zh",
    "HK": "zh",
}


def language_name(code: str) -> str:
    c = (code or "").strip().lower()
    return _LANGUAGE_NAMES.get(c) or (c.upper() if c else "English")


def default_language_for_country(country_code: str) -> tuple[str, str]:
    code = (country_code or "").strip().upper()
    lang = _COUNTRY_DEFAULT_LANGUAGE.get(code, "en")
    return lang, language_name(lang)


def locale_key(country_code: str, language: str) -> str:
    cc = (country_code or "").strip().upper() or "XX"
    lang = (language or "en").strip().lower() or "en"
    return f"{cc}:{lang}"


def locale_label(locale: dict[str, Any]) -> str:
    country = str(locale.get("country") or "").strip() or str(locale.get("country_code") or "").strip()
    lang = str(locale.get("language_name") or "").strip() or language_name(
        str(locale.get("language") or "en")
    )
    if country and lang:
        return f"{country}: {lang}"
    return lang or country or "Default"


def make_locale(
    *,
    country: str,
    country_code: str,
    language: str = "en",
    language_name_override: str = "",
) -> dict[str, Any]:
    mc, mid = resolve_primary_market(country, country_code)
    lang = (language or "en").strip().lower() or "en"
    if lang not in _LANGUAGE_NAMES and not language_name_override:
        lang = "en"
    name = (language_name_override or "").strip() or language_name(lang)
    return {
        "country": mc or (country or "").strip(),
        "country_code": mid or (country_code or "").strip().upper(),
        "language": lang,
        "language_name": name,
        "key": locale_key(mid or country_code, lang),
        "label": "",  # filled below
    }


def _with_label(locale: dict[str, Any]) -> dict[str, Any]:
    out = dict(locale)
    out["key"] = locale_key(
        str(out.get("country_code") or ""),
        str(out.get("language") or "en"),
    )
    out["label"] = locale_label(out)
    return out


def default_locale_from_market(market_country: str, market_country_code: str) -> dict[str, Any]:
    """Wizard step-1 market + English (product default for all historical audits)."""
    return _with_label(
        make_locale(
            country=market_country,
            country_code=market_country_code,
            language="en",
            language_name_override="English",
        )
    )


def normalize_prompt_locales(
    raw: Any,
    *,
    market_country: str = "",
    market_country_code: str = "",
) -> list[dict[str, Any]]:
    """
    Normalize configured locales. Always includes primary market + English first.
    Caps at ``MAX_PROMPT_LOCALES``. Dedupes by locale key.
    """
    default = default_locale_from_market(market_country, market_country_code)
    out: list[dict[str, Any]] = [default]
    seen = {str(default["key"])}

    if isinstance(raw, list):
        for item in raw:
            if not isinstance(item, dict):
                continue
            country = str(item.get("country") or "").strip()
            code = str(item.get("country_code") or item.get("country_id") or "").strip()
            lang = str(item.get("language") or "en").strip().lower() or "en"
            lang_name = str(item.get("language_name") or "").strip()
            if not country and not code:
                continue
            loc = _with_label(
                make_locale(
                    country=country,
                    country_code=code,
                    language=lang,
                    language_name_override=lang_name,
                )
            )
            key = str(loc["key"])
            if key in seen:
                continue
            seen.add(key)
            out.append(loc)
            if len(out) >= MAX_PROMPT_LOCALES:
                break

    return out


def locales_from_onboarding(ctx: dict[str, Any]) -> list[dict[str, Any]]:
    mcc = str(ctx.get("geo_market_country") or "").strip()
    mid = str(ctx.get("geo_market_country_code") or "").strip()
    if not mcc and not mid:
        pm = ctx.get("ga4_primary_market")
        if isinstance(pm, dict):
            mcc = str(pm.get("country") or "").strip()
            mid = str(pm.get("country_id") or "").strip()
    return normalize_prompt_locales(
        ctx.get("prompt_locales"),
        market_country=mcc,
        market_country_code=mid,
    )


def _same_market(
    a_country: str,
    a_code: str,
    b_country: str,
    b_code: str,
) -> bool:
    ac, aid = resolve_primary_market(a_country, a_code)
    bc, bid = resolve_primary_market(b_country, b_code)
    if aid and bid:
        return aid.upper() == bid.upper()
    if ac and bc:
        return ac.strip().lower() == bc.strip().lower()
    return False


def _deterministic_market_rewrite(
    prompts: list[str],
    *,
    source_country: str,
    source_code: str,
    target_country: str,
    target_code: str,
) -> list[str]:
    """Best-effort English geo phrase / country-name swap when Gemini is unavailable."""
    from prompt_suggest import geo_locator_phrase_for_market

    source_phrase = geo_locator_phrase_for_market(source_country, source_code)
    target_phrase = geo_locator_phrase_for_market(target_country, target_code)
    source_name = (source_country or "").strip()
    target_name = (target_country or "").strip()
    out: list[str] = []
    for prompt in prompts:
        text = prompt
        if source_phrase and target_phrase and source_phrase.lower() != target_phrase.lower():
            text = re.sub(re.escape(source_phrase), target_phrase, text, flags=re.IGNORECASE)
        if source_name and target_name and source_name.lower() != target_name.lower():
            text = re.sub(rf"\b{re.escape(source_name)}\b", target_name, text, flags=re.IGNORECASE)
        out.append(text)
    return out


def regenerate_prompts_for_language(
    prompts: list[str],
    *,
    target_language: str,
    target_language_name: str,
    market_country: str = "",
    market_country_code: str = "",
    source_market_country: str = "",
    source_market_country_code: str = "",
) -> list[str]:
    """
    Adapt prompts for a target market and/or language.

    - Same market + English → return originals (noop).
    - Different market (even if English) → rewrite geographic references for the new market.
    - Non-English → translate into the target language for that market.
    """
    cleaned = [str(p).strip() for p in prompts if str(p).strip()]
    if not cleaned:
        return []

    lang = (target_language or "en").strip().lower() or "en"
    mc, mid = resolve_primary_market(market_country, market_country_code)
    src_country = (source_market_country or "").strip() or mc
    src_code = (source_market_country_code or "").strip() or mid
    same_market = _same_market(src_country, src_code, mc, mid)

    if lang == "en" and same_market:
        return cleaned

    from prompt_suggest import (
        _gemini_generate,
        ensure_prompt_contains_geo_locator,
        geo_locator_phrase_for_market,
    )

    phrase = geo_locator_phrase_for_market(mc, mid)
    lang_label = (target_language_name or "").strip() or language_name(lang)
    source_label = src_country or src_code or "the source market"
    target_label = mc or mid or "the target market"

    if lang == "en":
        system = (
            "You adapt shopper search prompts for AI assistants. "
            f"Rewrite each prompt for shoppers in {target_label}"
            + (f" ({mid})" if mid else "")
            + f". The prompts were originally written for {source_label}"
            + (f" ({src_code})" if src_code else "")
            + ". Replace geographic references to the source market with natural equivalents "
            f"for {target_label} (for example change 'in Belgium' / 'Belgium' to "
            f"'{phrase or f'in {target_label}'}' / '{target_label}'). "
            "Preserve intent, product/category meaning, and brand-neutrality. "
            "Do not add brand names. Keep each prompt as a single concise question or request. "
            "Return ONLY a JSON array of strings, same length and order as the input."
        )
    else:
        system = (
            "You adapt shopper search prompts for AI assistants. "
            f"Rewrite each prompt into natural {lang_label} ({lang}) for shoppers in "
            f"{target_label}"
            + (f" ({mid})" if mid else "")
            + ". Preserve intent, product/category meaning, and brand-neutrality. "
            "Do not add brand names. Keep each prompt as a single concise question or request. "
            "Return ONLY a JSON array of strings, same length and order as the input."
        )
        if phrase:
            system += (
                f" Where geography matters, include an equivalent of {json.dumps(phrase)} "
                "in the target language (or keep that English geo phrase if it is the local convention)."
            )

    user = json.dumps(cleaned, ensure_ascii=False)
    try:
        raw = _gemini_generate(system_instruction=system, user_text=user)
    except Exception as exc:
        log.warning("Prompt locale adaptation failed (%s); using fallback", exc)
        if lang == "en" and not same_market:
            return _deterministic_market_rewrite(
                cleaned,
                source_country=src_country,
                source_code=src_code,
                target_country=mc,
                target_code=mid,
            )
        return cleaned

    parsed = _parse_string_array(raw, expected=len(cleaned))
    if not parsed:
        log.warning("Prompt locale adaptation returned unusable JSON; using fallback")
        if lang == "en" and not same_market:
            return _deterministic_market_rewrite(
                cleaned,
                source_country=src_country,
                source_code=src_code,
                target_country=mc,
                target_code=mid,
            )
        return cleaned

    if phrase:
        parsed = [ensure_prompt_contains_geo_locator(p, phrase) for p in parsed]
    return parsed


def _parse_string_array(raw: str, *, expected: int) -> list[str] | None:
    text = (raw or "").strip()
    if not text:
        return None
    fence = re.search(r"```(?:json)?\s*([\s\S]*?)```", text, flags=re.I)
    if fence:
        text = fence.group(1).strip()
    try:
        data = json.loads(text)
    except json.JSONDecodeError:
        start = text.find("[")
        end = text.rfind("]")
        if start < 0 or end <= start:
            return None
        try:
            data = json.loads(text[start : end + 1])
        except json.JSONDecodeError:
            return None
    if not isinstance(data, list):
        return None
    out = [str(x).strip() for x in data if str(x).strip()]
    if len(out) != expected:
        return None
    return out


def brand_share_from_live_probe(live: dict[str, Any] | None) -> float | None:
    if not isinstance(live, dict):
        return None
    for key in ("brand_share_pct", "brand_sov_pct", "mention_share_pct"):
        val = live.get(key)
        if isinstance(val, (int, float)):
            return float(val)
    aggregate = live.get("aggregate") if isinstance(live.get("aggregate"), dict) else {}
    for key in ("brand_share_pct", "brand_sov_pct", "mention_share_pct"):
        val = aggregate.get(key)
        if isinstance(val, (int, float)):
            return float(val)
    return None


def build_locale_spread(locale_probes: dict[str, Any]) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for key, entry in (locale_probes or {}).items():
        if not isinstance(entry, dict):
            continue
        locale = entry.get("locale") if isinstance(entry.get("locale"), dict) else {"key": key}
        live = entry.get("live_probe") if isinstance(entry.get("live_probe"), dict) else None
        share = brand_share_from_live_probe(live)
        rows.append(
            {
                "key": str(locale.get("key") or key),
                "label": locale_label(locale) if locale.get("country") or locale.get("language") else str(key),
                "country": str(locale.get("country") or ""),
                "country_code": str(locale.get("country_code") or ""),
                "language": str(locale.get("language") or ""),
                "language_name": str(locale.get("language_name") or ""),
                "brand_share_pct": share,
                "prompt_count": len(live.get("per_prompt") or []) if isinstance(live, dict) else 0,
            }
        )
    rows.sort(key=lambda r: (-(r["brand_share_pct"] if r["brand_share_pct"] is not None else -1), r["label"]))
    return rows
