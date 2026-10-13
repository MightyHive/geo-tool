"""Shared prompt selection for live probes, sentiment, and SOV."""

from __future__ import annotations

from typing import Any

CUSTOM_PROMPTS_LABEL = "Custom prompts"
PROMPTS_PER_PRODUCT_LINE = 5
MAX_PROBE_PROMPTS_TOTAL = 25
MAX_CUSTOM_PROMPTS = 15
# Persona prompts have their own budget, separate from topic prompts.
MAX_PERSONA_PROMPTS_TOTAL = 25
PERSONA_TAG_PREFIX = "Persona: "


def is_custom_prompts_category(label: str) -> bool:
    return label.strip().lower() == CUSTOM_PROMPTS_LABEL.lower()


def _normalize_row(row: dict[str, Any]) -> dict[str, Any] | None:
    label = str(row.get("product_or_service") or "").strip()
    prs = row.get("prompts")
    if not label or not isinstance(prs, list):
        return None
    prompts = [str(p).strip() for p in prs if str(p).strip()]
    if not prompts:
        return None
    prompt_set = set(prompts)
    raw_tags = row.get("prompt_tags") if isinstance(row.get("prompt_tags"), dict) else {}
    prompt_tags = {
        str(prompt): [str(tag).strip() for tag in tags if str(tag).strip()]
        for prompt, tags in raw_tags.items()
        if str(prompt) in prompt_set and isinstance(tags, list)
    }
    custom_prompts = [
        str(prompt).strip()
        for prompt in (row.get("custom_prompts") or [])
        if str(prompt).strip() in prompt_set
    ]
    if bool(row.get("is_custom_topic")) or is_custom_prompts_category(label):
        custom_prompts = list(prompts)
    return {
        "product_or_service": label,
        "prompts": prompts,
        "prompt_tags": prompt_tags,
        "prompt_personas": clean_prompt_personas(row.get("prompt_personas"), prompt_set),
        "custom_prompts": custom_prompts,
        "is_custom_topic": bool(row.get("is_custom_topic")) or is_custom_prompts_category(label),
    }


def clean_prompt_personas(raw: Any, prompt_set: set[str] | None = None) -> dict[str, str]:
    """Normalise a ``prompt -> persona name`` map, optionally limited to known prompts."""
    if not isinstance(raw, dict):
        return {}
    out: dict[str, str] = {}
    for prompt, persona in raw.items():
        key = str(prompt).strip()
        name = str(persona or "").strip()
        if not key or not name:
            continue
        if prompt_set is not None and key not in prompt_set:
            continue
        out[key] = name
    return out


def _selected_row(
    row: dict[str, Any],
    prompts: list[str],
    *,
    custom: bool = False,
    custom_prompts: list[str] | None = None,
) -> dict[str, Any]:
    prompt_set = set(prompts)
    return {
        "product_or_service": row["product_or_service"],
        "prompts": prompts,
        "prompt_tags": {
            prompt: tags
            for prompt, tags in (row.get("prompt_tags") or {}).items()
            if prompt in prompt_set
        },
        "prompt_personas": {
            prompt: persona
            for prompt, persona in (row.get("prompt_personas") or {}).items()
            if prompt in prompt_set
        },
        "custom_prompts": prompts if custom else list(custom_prompts or []),
        "is_custom_topic": bool(row.get("is_custom_topic")),
    }


def select_prompts_for_probing(
    rows: list[dict[str, Any]],
) -> tuple[list[str], list[dict[str, Any]]]:
    """
    Select prompts to run in live AI probes.

    Custom prompts are always included and do not count toward ``MAX_PROBE_PROMPTS_TOTAL``.
    Other product lines: up to ``PROMPTS_PER_PRODUCT_LINE`` each, capped at
    ``MAX_PROBE_PROMPTS_TOTAL`` total.
    Persona prompts (``prompt_personas``) have their own budget of
    ``MAX_PERSONA_PROMPTS_TOTAL``, taken round-robin across personas, and are merged
    back into their topic row.
    """
    normalized: list[dict[str, Any]] = []
    for row in rows:
        if not isinstance(row, dict):
            continue
        n = _normalize_row(row)
        if n:
            normalized.append(n)

    custom_rows = [r for r in normalized if r.get("is_custom_topic")]
    other_rows = [r for r in normalized if not r.get("is_custom_topic")]

    probed_other: list[dict[str, Any]] = []
    flat_other: list[str] = []
    non_custom_count = 0

    for row in other_rows:
        if non_custom_count >= MAX_PROBE_PROMPTS_TOTAL:
            break
        selected: list[str] = []
        custom_prompt_set = set(row.get("custom_prompts") or [])
        persona_prompt_set = set(row.get("prompt_personas") or {})
        for prompt in row["prompts"]:
            if prompt in custom_prompt_set or prompt in persona_prompt_set:
                continue
            if len(selected) >= PROMPTS_PER_PRODUCT_LINE:
                break
            if non_custom_count >= MAX_PROBE_PROMPTS_TOTAL:
                break
            selected.append(prompt)
            non_custom_count += 1
        if selected:
            probed_other.append(_selected_row(row, selected))
            flat_other.extend(selected)

    # Persona prompts: separate budget, round-robin across personas.
    persona_queues: dict[str, list[tuple[int, str]]] = {}
    for idx, row in enumerate(other_rows):
        custom_prompt_set = set(row.get("custom_prompts") or [])
        personas = row.get("prompt_personas") or {}
        for prompt in row["prompts"]:
            persona = personas.get(prompt)
            if persona and prompt not in custom_prompt_set:
                persona_queues.setdefault(persona, []).append((idx, prompt))
    chosen_persona: dict[int, set[str]] = {}
    persona_count = 0
    while persona_count < MAX_PERSONA_PROMPTS_TOTAL and any(persona_queues.values()):
        for queue in persona_queues.values():
            if not queue or persona_count >= MAX_PERSONA_PROMPTS_TOTAL:
                continue
            idx, prompt = queue.pop(0)
            chosen_persona.setdefault(idx, set()).add(prompt)
            persona_count += 1

    flat_persona: list[str] = []
    if chosen_persona:
        by_label = {r["product_or_service"]: r for r in probed_other}
        for idx, row in enumerate(other_rows):
            picks = chosen_persona.get(idx)
            if not picks:
                continue
            persona_selected = [p for p in row["prompts"] if p in picks]
            flat_persona.extend(persona_selected)
            existing = by_label.get(row["product_or_service"])
            if existing is not None:
                merged = existing["prompts"] + persona_selected
                by_label[row["product_or_service"]] = _selected_row(row, merged)
            else:
                by_label[row["product_or_service"]] = _selected_row(row, persona_selected)
        order = [r["product_or_service"] for r in other_rows]
        probed_other = [by_label[label] for label in dict.fromkeys(order) if label in by_label]

    probed_custom: list[dict[str, Any]] = []
    flat_custom: list[str] = []
    for row in normalized:
        selected = list(row["prompts"] if row.get("is_custom_topic") else row.get("custom_prompts") or [])[:MAX_CUSTOM_PROMPTS]
        if selected:
            probed_custom.append(_selected_row(row, selected, custom=True))
            flat_custom.extend(selected)

    probed_rows = probed_other + probed_custom
    if flat_persona:
        # Keep flat order aligned with probed_rows (positional locale mapping relies on it).
        flat_other = [p for r in probed_other for p in r["prompts"]]
    flat = flat_other + flat_custom
    return flat, probed_rows


def probed_category_labels(probed_rows: list[dict[str, Any]]) -> set[str]:
    return {
        str(r.get("product_or_service") or "").strip()
        for r in probed_rows
        if str(r.get("product_or_service") or "").strip()
    }


def select_flat_prompts_for_probing(flat_prompts: list[str]) -> list[str]:
    """Legacy flat-prompt path (no product/service rows)."""
    out: list[str] = []
    for prompt in flat_prompts:
        s = str(prompt).strip()
        if not s:
            continue
        out.append(s)
        if len(out) >= MAX_PROBE_PROMPTS_TOTAL:
            break
    return out
