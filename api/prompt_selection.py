"""Shared prompt selection for live probes, sentiment, and SOV."""

from __future__ import annotations

from typing import Any

CUSTOM_PROMPTS_LABEL = "Custom prompts"
PROMPTS_PER_PRODUCT_LINE = 5
MAX_PROBE_PROMPTS_TOTAL = 25
MAX_CUSTOM_PROMPTS = 15


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
    return {"product_or_service": label, "prompts": prompts}


def select_prompts_for_probing(
    rows: list[dict[str, Any]],
) -> tuple[list[str], list[dict[str, Any]]]:
    """
    Select prompts to run in live AI probes.

    Custom prompts are always included and do not count toward ``MAX_PROBE_PROMPTS_TOTAL``.
    Other product lines: up to ``PROMPTS_PER_PRODUCT_LINE`` each, capped at
    ``MAX_PROBE_PROMPTS_TOTAL`` total.
    """
    normalized: list[dict[str, Any]] = []
    for row in rows:
        if not isinstance(row, dict):
            continue
        n = _normalize_row(row)
        if n:
            normalized.append(n)

    custom_rows = [r for r in normalized if is_custom_prompts_category(r["product_or_service"])]
    other_rows = [r for r in normalized if not is_custom_prompts_category(r["product_or_service"])]

    probed_other: list[dict[str, Any]] = []
    flat_other: list[str] = []
    non_custom_count = 0

    for row in other_rows:
        if non_custom_count >= MAX_PROBE_PROMPTS_TOTAL:
            break
        selected: list[str] = []
        for prompt in row["prompts"]:
            if len(selected) >= PROMPTS_PER_PRODUCT_LINE:
                break
            if non_custom_count >= MAX_PROBE_PROMPTS_TOTAL:
                break
            selected.append(prompt)
            non_custom_count += 1
        if selected:
            probed_other.append({"product_or_service": row["product_or_service"], "prompts": selected})
            flat_other.extend(selected)

    probed_custom: list[dict[str, Any]] = []
    flat_custom: list[str] = []
    for row in custom_rows:
        selected = row["prompts"][:MAX_CUSTOM_PROMPTS]
        if selected:
            probed_custom.append({"product_or_service": row["product_or_service"], "prompts": selected})
            flat_custom.extend(selected)

    probed_rows = probed_other + probed_custom
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
