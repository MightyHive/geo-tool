"""SOV metrics from live probe JSON (mirrors web/src/lib/promptPerformanceSov.ts)."""

from __future__ import annotations

import json
import re
import urllib.parse
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

LIVE_PROBE_FILE = "prompt_performance_live_probe.json"


def _read_json(path: Path) -> dict[str, Any] | None:
    if not path.is_file():
        return None
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    return raw if isinstance(raw, dict) else None


def _normalize_competitor_url(raw: str) -> str:
    s = (raw or "").strip()
    if not s:
        return ""
    if not re.match(r"^[a-zA-Z][a-zA-Z0-9+.-]*://", s):
        s = "https://" + s
    return s


def _match_tokens(tc: dict[str, str]) -> list[str]:
    tokens: set[str] = set()
    brand = str(tc.get("competitor_brand") or "").strip().lower()
    if len(brand) >= 2:
        tokens.add(brand)
    url = _normalize_competitor_url(str(tc.get("competitor_website") or ""))
    if url:
        try:
            host = (urllib.parse.urlparse(url).hostname or "").replace("www.", "").lower()
            if len(host) >= 3:
                tokens.add(host)
                base = host.split(".")[0]
                if base and len(base) >= 3:
                    tokens.add(base)
        except Exception:
            pass
    return list(tokens)


def _detail_key_matches(detail_key: str, tc: dict[str, str]) -> bool:
    dk = detail_key.lower().strip()
    if not dk:
        return False
    for token in _match_tokens(tc):
        if dk == token:
            return True
        if len(token) >= 3 and len(dk) >= 3 and (token in dk or dk in token):
            return True
    return False


def _tracked_hits(scores: dict[str, Any], tc: dict[str, str]) -> int:
    hits = 0
    detail = scores.get("competitor_detail")
    if not isinstance(detail, dict):
        return 0
    for key, count in detail.items():
        if _detail_key_matches(str(key), tc):
            hits += int(float(count or 0))
    return hits


def _brand_share_pct(scores: dict[str, Any] | None) -> float:
    if not scores:
        return 0.0
    brand = float(scores.get("brand_signal") or 0)
    total = brand + float(scores.get("competitors_combined_hits") or 0)
    if total <= 0:
        return 0.0
    return 100.0 * brand / total


def _tracked_sov_in_reply(scores: dict[str, Any] | None, tc: dict[str, str]) -> float | None:
    if not scores:
        return None
    hits = _tracked_hits(scores, tc)
    if hits <= 0:
        return None
    brand = float(scores.get("brand_signal") or 0)
    total = brand + float(scores.get("competitors_combined_hits") or 0)
    if total <= 0:
        return None
    return 100.0 * hits / total


def _avg_tracked_competitor_sov(scores: dict[str, Any] | None, tracked: list[dict[str, str]]) -> float:
    if not tracked or not scores:
        return 0.0
    pcts = [_tracked_sov_in_reply(scores, tc) for tc in tracked]
    vals = [p for p in pcts if p is not None]
    if not vals:
        return 0.0
    return sum(vals) / len(vals)


def _scores_from_row(row: dict[str, Any], platform: str) -> dict[str, Any] | None:
    key = "mention_scores_gemini" if platform == "gemini" else "mention_scores_openai"
    raw = row.get(key)
    return raw if isinstance(raw, dict) else None


def _merge_mention_scores(rows: list[dict[str, Any]], platform: str) -> dict[str, Any]:
    brand = 0.0
    comp = 0.0
    detail: dict[str, float] = {}
    for row in rows:
        s = _scores_from_row(row, platform)
        if not s:
            continue
        brand += float(s.get("brand_signal") or 0)
        comp += float(s.get("competitors_combined_hits") or 0)
        d = s.get("competitor_detail")
        if isinstance(d, dict):
            for k, v in d.items():
                detail[str(k)] = detail.get(str(k), 0.0) + float(v or 0)
    return {"brand_signal": brand, "competitors_combined_hits": comp, "competitor_detail": detail}


def _mean_brand_and_competitor_sov(
    per_prompt: list[dict[str, Any]],
    tracked: list[dict[str, str]],
) -> tuple[float, float]:
    if not per_prompt:
        return 0.0, 0.0
    gem = _merge_mention_scores(per_prompt, "gemini")
    oai = _merge_mention_scores(per_prompt, "openai")
    brand = (_brand_share_pct(gem) + _brand_share_pct(oai)) / 2.0
    comp = (_avg_tracked_competitor_sov(gem, tracked) + _avg_tracked_competitor_sov(oai, tracked)) / 2.0
    return round(brand, 2), round(comp, 2)


def _normalize_pss_rows(rows: Any) -> list[dict[str, Any]]:
    if not isinstance(rows, list):
        return []
    out: list[dict[str, Any]] = []
    for r in rows:
        if not isinstance(r, dict):
            continue
        label = str(r.get("product_or_service") or "").strip()
        prs = r.get("prompts")
        if not label or not isinstance(prs, list):
            continue
        ps = [str(p).strip() for p in prs if str(p).strip()]
        if ps:
            out.append({"product_or_service": label, "prompts": ps})
    return out


def _split_live_by_product(
    pss_rows: list[dict[str, Any]],
    per_prompt: list[dict[str, Any]],
) -> dict[str, list[dict[str, Any]]]:
    from api.prompt_selection import select_prompts_for_probing

    _, probed_rows = select_prompts_for_probing(pss_rows)
    rows_for_split = probed_rows if probed_rows else pss_rows
    flat: list[str] = []
    meta: list[str] = []
    for r in rows_for_split:
        pos = str(r.get("product_or_service") or "")
        for p in r.get("prompts") or []:
            s = str(p).strip()
            if s:
                flat.append(s)
                meta.append(pos)
    out: dict[str, list[dict[str, Any]]] = {}
    for i, prod in enumerate(meta):
        if i >= len(per_prompt):
            break
        out.setdefault(prod, []).append(per_prompt[i])
    return out


def collect_sov_history(
    audit_dir: Path,
    base_url: str,
    tracked_competitors: list[dict[str, str]],
    *,
    product_labels: list[str] | None = None,
    max_dirs_scan: int = 400,
) -> tuple[list[dict[str, Any]], dict[str, list[dict[str, Any]]]]:
    """SOV time series from sibling audits for the same site (same parent folder)."""
    from api import geo_services as geo

    normalize_base_fn = geo.load_create_report().normalize_base

    try:
        target_norm = normalize_base_fn(str(base_url or "").strip())
    except Exception:
        return [], {}

    parent = audit_dir.resolve().parent
    audit_root = audit_dir.resolve()
    if not parent.is_dir():
        return [], {}

    hits: list[tuple[float, Path, list[dict[str, Any]], list[dict[str, Any]]]] = []
    scanned = 0
    for child in parent.iterdir():
        if scanned >= max_dirs_scan:
            break
        if not child.is_dir():
            continue
        summary_path = child / "audit_summary.json"
        probe_path = child / LIVE_PROBE_FILE
        if not summary_path.is_file() or not probe_path.is_file():
            continue
        scanned += 1
        try:
            other = _read_json(summary_path)
        except Exception:
            continue
        if not other:
            continue
        bu = str(other.get("base_url") or "").strip()
        try:
            if normalize_base_fn(bu) != target_norm:
                continue
        except Exception:
            continue
        saved = _read_json(probe_path)
        if not saved:
            continue
        live = saved.get("live_probe")
        if not isinstance(live, dict):
            continue
        per = live.get("per_prompt")
        if not isinstance(per, list) or not per:
            continue
        per_clean = [p for p in per if isinstance(p, dict)]
        if not per_clean:
            continue

        ob = _read_json(child / "onboarding_context.json")
        pss_path = child / "products_and_services.json"
        pss_rows: list[dict[str, Any]] = []
        if pss_path.is_file():
            pss_raw = _read_json(pss_path)
            if pss_raw:
                pss_rows = _normalize_pss_rows(pss_raw.get("rows"))
        if not pss_rows and ob:
            pss_rows = _normalize_pss_rows(ob.get("products_and_services_rows"))

        mtime = probe_path.stat().st_mtime
        hits.append((mtime, child.resolve(), per_clean, pss_rows))

    hits.sort(key=lambda x: x[0])

    overall: list[dict[str, Any]] = []
    by_product: dict[str, list[dict[str, Any]]] = {}
    want_products = [str(p).strip() for p in (product_labels or []) if str(p).strip()]

    for mtime, path, per_clean, pss_rows in hits:
        brand, comp = _mean_brand_and_competitor_sov(per_clean, tracked_competitors)
        point = {
            "date": datetime.fromtimestamp(mtime, tz=UTC).strftime("%Y-%m-%d"),
            "datetime": datetime.fromtimestamp(mtime, tz=UTC).strftime("%Y-%m-%d %H:%M UTC"),
            "audit_dir": str(path),
            "is_current": path == audit_root,
            "brand_share_pct": brand,
            "competitor_avg_sov_pct": comp,
        }
        overall.append(point)

        if pss_rows and want_products:
            split = _split_live_by_product(pss_rows, per_clean)
            for prod in want_products:
                rows = split.get(prod) or []
                if not rows:
                    continue
                b2, c2 = _mean_brand_and_competitor_sov(rows, tracked_competitors)
                by_product.setdefault(prod, []).append(
                    {
                        **point,
                        "brand_share_pct": b2,
                        "competitor_avg_sov_pct": c2,
                    }
                )

    return overall, by_product
