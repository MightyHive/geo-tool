#!/usr/bin/env python3
"""
Off-site brand presence checks for GEO audits (skills/brand-visbility.md).

Wikipedia (API + multi-query variants), YouTube (handles + results-page parse),
Reddit (search API + optional subreddit), LinkedIn (expanded company slug set).
"""

from __future__ import annotations

import html as html_mod
import json
import re
import ssl
import time
import urllib.error
import urllib.parse
import urllib.request
from typing import Any


def _https_context() -> ssl.SSLContext | None:
    try:
        import certifi  # type: ignore[import-untyped]

        return ssl.create_default_context(cafile=certifi.where())
    except ImportError:
        return None


_HTTPS_CTX = _https_context()

# Descriptive UA per https://meta.wikimedia.org/wiki/User-Agent_policy
USER_AGENT = (
    "GEOBrandVisibility/1.0 (+https://example.com/contact; "
    "automated brand-visibility audit; respects robots/rate limits)"
)
TIMEOUT = 22

PLATFORM_IMPACT_BLURB: dict[str, str] = {
    "Wikipedia": "Very high — frequent citation source for ChatGPT and other models.",
    "YouTube": "Medium–high — rich media and entity linkage.",
    "Reddit": "Medium — community discussions models may weight.",
    "LinkedIn": "High — company and executive verification signals.",
}


def derive_brand_from_base(base_url: str) -> str:
    """Best-effort display name from hostname (e.g. example-parts.com → Example Parts)."""
    u = base_url.strip()
    if not u:
        return "Brand"
    if "://" not in u:
        u = "https://" + u
    host = urllib.parse.urlparse(u).netloc.lower()
    if host.startswith("www."):
        host = host[4:]
    host = host.split(":")[0]
    first = host.split(".")[0] if host else ""
    guess = re.sub(r"[-_]+", " ", first).strip()
    return guess.title() if guess else "Brand"


def _normalize(s: str) -> str:
    return re.sub(r"[^a-z0-9]+", " ", s.lower()).strip()


def _alnum_compact(s: str) -> str:
    return re.sub(r"[^a-z0-9]", "", s.lower())


def _slugs(brand: str) -> list[str]:
    b = brand.lower().strip()
    s1 = re.sub(r"[^a-z0-9]+", "-", b).strip("-")
    out: list[str] = []
    if s1:
        out.append(s1)
        if "-" in s1:
            out.append(s1.replace("-", ""))
    return list(dict.fromkeys(out)) or ["brand"]


def _search_variants_for_brand(brand: str, base_url: str) -> list[str]:
    """Phrases to try in Wikipedia / Reddit search (hostname often lacks spaces)."""
    seen: set[str] = set()
    out: list[str] = []

    def add(s: str) -> None:
        s = (s or "").strip()
        if not s:
            return
        key = s.lower()
        if key not in seen:
            seen.add(key)
            out.append(s)

    add(brand)
    u = base_url.strip()
    if u and "://" not in u:
        u = "https://" + u
    host = urllib.parse.urlparse(u).netloc.lower().split(":")[0]
    if host.startswith("www."):
        host = host[4:]
    label = host.split(".")[0] if host else ""
    if label:
        add(label.replace("-", " ").title())
        compact = _alnum_compact(label)
        m = re.match(r"^([a-z]+)(carparts)$", compact)
        if m:
            add(f"{m.group(1).title()} Car Parts")
        m2 = re.match(r"^([a-z]+)(auto|motor)(parts)?$", compact)
        if m2 and m2.group(1) not in ("carparts",):
            frag = m2.group(0)
            if "carparts" in frag:
                add(f"{m2.group(1).title()} Car Parts")
    # Prefer spaced trade names before compact host tokens for slugs / API order.
    spaced = [x for x in out if " " in x]
    rest = [x for x in out if " " not in x]
    return spaced + rest


def _urlopen(url: str, req: urllib.request.Request):
    kw: dict[str, Any] = {"timeout": TIMEOUT}
    if _HTTPS_CTX and urllib.parse.urlparse(url).scheme == "https":
        kw["context"] = _HTTPS_CTX
    return urllib.request.urlopen(req, **kw)


def _fetch_json(url: str) -> Any | None:
    req = urllib.request.Request(
        url, headers={"User-Agent": USER_AGENT, "Accept": "application/json"}
    )
    try:
        with _urlopen(url, req) as r:
            return json.loads(r.read().decode("utf-8", errors="replace"))
    except (OSError, ValueError, json.JSONDecodeError):
        return None


def _fetch_html(url: str) -> tuple[int | None, str]:
    req = urllib.request.Request(
        url,
        headers={
            "User-Agent": USER_AGENT,
            "Accept": "text/html,application/xhtml+xml;q=0.9,*/*;q=0.8",
            "Accept-Language": "en-GB,en;q=0.9",
        },
    )
    try:
        with _urlopen(url, req) as r:
            body = r.read(200_000).decode("utf-8", errors="replace")
            return getattr(r, "status", 200), body
    except urllib.error.HTTPError as e:
        try:
            chunk = e.read(80_000).decode("utf-8", errors="replace") if e.fp else ""
        except OSError:
            chunk = ""
        return e.code, chunk
    except OSError:
        return None, ""


def _title_matches_brand_variants(title: str, variants: list[str]) -> bool:
    tn = _normalize(title)
    tn_compact = _alnum_compact(title)
    for v in variants:
        vn = _normalize(v)
        if not vn:
            continue
        if vn in tn:
            return True
        vtok = [t for t in vn.split() if len(t) > 1]
        if len(vtok) >= 2 and all(t in tn for t in vtok):
            return True
        vc = _alnum_compact(v)
        if len(vc) >= 5 and vc == tn_compact:
            return True
        if len(vc) >= 6 and vc in tn_compact:
            return True
    return False


def _wikipedia_probe(
    brand: str, base_url: str, delay: float
) -> tuple[bool, str, str | None]:
    variants = _search_variants_for_brand(brand, base_url)
    merged: list[dict[str, Any]] = []
    seen_titles: set[str] = set()
    for q in variants[:8]:
        wp_url = (
            "https://en.wikipedia.org/w/api.php?action=query&list=search"
            f"&srsearch={urllib.parse.quote_plus(q)}&format=json&srlimit=12"
        )
        data = _fetch_json(wp_url)
        time.sleep(delay)
        for hit in (data or {}).get("query", {}).get("search") or []:
            t = hit.get("title") or ""
            if t and t not in seen_titles:
                seen_titles.add(t)
                merged.append(hit)
        if merged and any(
            _title_matches_brand_variants(h.get("title") or "", variants) for h in merged
        ):
            break

    for hit in merged:
        title = hit.get("title") or ""
        if title and _title_matches_brand_variants(title, variants):
            safe = title.replace(" ", "_")
            wurl = f"https://en.wikipedia.org/wiki/{urllib.parse.quote(safe, safe='')}"
            return True, f"Article: {title}", wurl

    if merged:
        t0 = merged[0].get("title", "?")
        return False, f"No title matched brand variants; top hit: {t0}.", None
    return False, "No Wikipedia search results for tried queries.", None


def _youtube_handle_candidates(brand: str, base_url: str) -> list[str]:
    handles: list[str] = []
    seen: set[str] = set()

    def add(h: str) -> None:
        h = h.strip().strip("@")
        if h and h.lower() not in seen:
            seen.add(h.lower())
            handles.append(h)

    for slug in _slugs(brand):
        add(slug)
        add(slug.replace("-", ""))
        if slug:
            add(slug + "official")
            add(slug.replace("-", "") + "official")
    for phrase in _search_variants_for_brand(brand, base_url):
        s = re.sub(r"[^a-zA-Z0-9]+", "", phrase)
        if len(s) >= 4:
            add(s)
            add(s + "Official")
            if s.isascii() and s[:1].isalpha():
                titled = s[0].upper() + s[1:] + "Official"
                add(titled)
    return handles[:24]


def _youtube_parse_handles_from_html(body: str) -> list[str]:
    urls: list[str] = []
    seen: set[str] = set()
    for m in re.finditer(
        r'https://www\.youtube\.com/(@[a-zA-Z0-9_-]+)', body, re.IGNORECASE
    ):
        u = m.group(0).rstrip("/")
        if u.lower() not in seen:
            seen.add(u.lower())
            urls.append(u)
    for m in re.finditer(r'href="(https://www\.youtube\.com/@[^"\\?&]+)', body):
        u = m.group(1).rstrip("/")
        if u.lower() not in seen:
            seen.add(u.lower())
            urls.append(u)
    for m in re.finditer(r'"(/@[a-zA-Z0-9_-]+)"', body):
        u = "https://www.youtube.com" + m.group(1)
        if u.lower() not in seen:
            seen.add(u.lower())
            urls.append(u)
    return urls


def _youtube_channel_looks_real(body: str, brand_variants: list[str]) -> bool:
    low = body.lower()
    if any(
        x in low
        for x in (
            "this channel doesn't exist",
            "this channel does not exist",
            "the channel you searched for does not exist",
        )
    ):
        return False
    if "subscriber" in low or "subscribers" in low:
        return True
    if '"channelId"' in body or '"externalId"' in body:
        return True
    bv = [_alnum_compact(v) for v in brand_variants if _alnum_compact(v)]
    compact_body = _alnum_compact(body[:50_000])
    for b in bv:
        if len(b) >= 5 and b in compact_body:
            return True
    return False


def _youtube_probe(
    brand: str, base_url: str, delay: float
) -> tuple[bool, str, str | None]:
    brand_variants = _search_variants_for_brand(brand, base_url)
    absent = (
        "this channel doesn't exist",
        "this channel does not exist",
        "404. that's an error",
        "the channel you searched for does not exist",
    )
    for handle in _youtube_handle_candidates(brand, base_url):
        path = f"https://www.youtube.com/@{handle}"
        status, body = _fetch_html(path)
        time.sleep(delay)
        low = body.lower()
        if status == 404 or status is None:
            continue
        if any(a in low for a in absent):
            continue
        if status == 200 and _youtube_channel_looks_real(body, brand_variants):
            return True, f"Channel @{handle} resolves.", path

    for qphrase in brand_variants[:3]:
        q = urllib.parse.quote_plus(qphrase + " official channel")
        search_url = f"https://www.youtube.com/results?search_query={q}"
        status, body = _fetch_html(search_url)
        time.sleep(delay)
        if status != 200 or not body:
            continue
        for cand in _youtube_parse_handles_from_html(body)[:6]:
            st2, body2 = _fetch_html(cand)
            time.sleep(delay)
            if st2 == 200 and _youtube_channel_looks_real(body2, brand_variants):
                return True, "Found via YouTube search results.", cand

    return False, "No channel matched handles or search results; verify manually.", None


def _reddit_thread_json_url(thread_url: str) -> str:
    u = (thread_url or "").strip().rstrip("/")
    if not u:
        return u
    if u.endswith(".json"):
        return u
    return u + ".json"


def _reddit_walk_comments_collect(
    children: list[Any],
    out: list[tuple[int, str]],
    *,
    depth: int,
    max_depth: int,
) -> None:
    if depth > max_depth:
        return
    for ch in children or []:
        if not isinstance(ch, dict) or ch.get("kind") != "t1":
            continue
        d = ch.get("data") or {}
        body = (d.get("body") or "").strip()
        if not body or body in ("[deleted]", "[removed]"):
            continue
        author = (d.get("author") or "").lower()
        if d.get("stickied") and ("automod" in author or author == "auto moderator"):
            continue
        try:
            score = int(d.get("score") or 0)
        except (TypeError, ValueError):
            score = 0
        plain = html_mod.unescape(re.sub(r"\s+", " ", body))[:2000]
        if plain:
            out.append((score, plain))
        replies = d.get("replies")
        if isinstance(replies, dict) and depth < max_depth:
            rdata = replies.get("data") or {}
            _reddit_walk_comments_collect(
                rdata.get("children") or [],
                out,
                depth=depth + 1,
                max_depth=max_depth,
            )


def _reddit_top_comment_bodies_from_thread_json(payload: Any, *, max_bodies: int = 15) -> list[str]:
    if not isinstance(payload, list) or len(payload) < 2:
        return []
    comments_listing = payload[1]
    if not isinstance(comments_listing, dict):
        return []
    cdata = comments_listing.get("data") or {}
    raw_children = cdata.get("children") or []
    scored: list[tuple[int, str]] = []
    _reddit_walk_comments_collect(raw_children, scored, depth=0, max_depth=3)
    scored.sort(key=lambda x: (-x[0], -len(x[1])))
    bodies: list[str] = []
    seen: set[str] = set()
    for _sc, text in scored:
        key = text[:240].lower()
        if key in seen:
            continue
        seen.add(key)
        bodies.append(text)
        if len(bodies) >= max_bodies:
            break
    return bodies


def _reddit_fetch_top_comments(thread_url: str, delay: float) -> list[str]:
    jurl = _reddit_thread_json_url(thread_url)
    data = _fetch_json(jurl)
    time.sleep(delay)
    if not data:
        return []
    return _reddit_top_comment_bodies_from_thread_json(data, max_bodies=15)


def _reddit_search_top_threads(
    brand: str, base_url: str, delay: float, *, limit: int = 3
) -> list[dict[str, str]]:
    """Up to `limit` search hits whose title/selftext match brand variants (unique permalinks)."""
    variants = _search_variants_for_brand(brand, base_url)[:5]
    found: list[dict[str, str]] = []
    seen_perm: set[str] = set()
    for q in variants:
        if len(found) >= limit:
            break
        qs = urllib.parse.quote_plus(q)
        url = f"https://www.reddit.com/search.json?q={qs}&limit=12&sort=relevance&type=link"
        data = _fetch_json(url)
        time.sleep(delay)
        if not data:
            continue
        children = (data.get("data") or {}).get("children") or []
        for ch in children:
            if len(found) >= limit:
                break
            d = ch.get("data") or {}
            permalink = d.get("permalink")
            if not permalink or not isinstance(permalink, str):
                continue
            perm_key = permalink.split("?")[0].rstrip("/")
            if perm_key in seen_perm:
                continue
            title = (d.get("title") or "").strip()
            sub = (d.get("subreddit") or "").strip()
            full = f"https://www.reddit.com{permalink}"
            stext = _normalize(d.get("selftext", "") or "")
            combined = f"{title} {stext}"
            if _title_matches_brand_variants(combined, variants):
                seen_perm.add(perm_key)
                found.append(
                    {
                        "title": title,
                        "subreddit": sub,
                        "permalink": permalink,
                        "url": full.split("?")[0].rstrip("/"),
                    }
                )
    return found


_sentiment_pipeline_cache: dict[str, Any] = {"pipe": None, "err": None}


def _get_distilbert_sentiment_pipeline() -> tuple[Any | None, str | None]:
    """Lazy-load DistilBERT SST-2; first call may download weights."""
    if _sentiment_pipeline_cache["err"] is not None:
        return None, str(_sentiment_pipeline_cache["err"])
    if _sentiment_pipeline_cache["pipe"] is not None:
        return _sentiment_pipeline_cache["pipe"], None
    try:
        from transformers import pipeline  # type: ignore[import-untyped]

        _sentiment_pipeline_cache["pipe"] = pipeline(
            "sentiment-analysis",
            model="distilbert-base-uncased-finetuned-sst-2-english",
            truncation=True,
            max_length=512,
            device=-1,
        )
        return _sentiment_pipeline_cache["pipe"], None
    except Exception as e:  # noqa: BLE001 — surface import/model errors to audit JSON
        _sentiment_pipeline_cache["err"] = str(e)
        return None, str(e)


def _sentiment_bucket(positive_prob: float) -> str:
    if positive_prob >= 0.6:
        return "positive"
    if positive_prob <= 0.4:
        return "negative"
    return "mixed"


def _run_sentiment_on_thread(pipe: Any, title: str, comment_bodies: list[str]) -> dict[str, Any]:
    parts: list[str] = []
    if title.strip():
        parts.append(title.strip())
    parts.extend(comment_bodies[:15])
    text = "\n\n".join(parts).strip()
    if not text:
        return {
            "sentiment_label": "mixed",
            "sentiment_positive": 0.5,
            "sentiment_negative": 0.5,
            "sentiment_note": "No text to score.",
        }
    out = pipe(text[:12000])[0]
    lab = str(out.get("label") or "").upper()
    conf = float(out.get("score") or 0.0)
    if lab == "POSITIVE":
        pos_p = conf
    elif lab == "NEGATIVE":
        pos_p = 1.0 - conf
    else:
        pos_p = 0.5
    bucket = _sentiment_bucket(pos_p)
    return {
        "sentiment_label": bucket,
        "sentiment_positive": round(pos_p, 4),
        "sentiment_negative": round(1.0 - pos_p, 4),
        "sentiment_note": f"Model: {lab} ({conf:.0%} conf.) on title + top comments.",
    }


def _reddit_scan_search_threads_with_sentiment(
    brand: str, base_url: str, delay: float
) -> dict[str, Any]:
    """
    Top matching threads from Reddit search + DistilBERT sentiment on title + top comments.
    Returns keys merged onto the Reddit platform row.
    """
    threads_raw = _reddit_search_top_threads(brand, base_url, delay, limit=3)
    if not threads_raw:
        return {
            "reddit_threads": [],
            "reddit_sentiment_summary": "",
            "reddit_sentiment_model": "distilbert-base-uncased-finetuned-sst-2-english",
            "reddit_sentiment_error": "",
        }

    pipe, perr = _get_distilbert_sentiment_pipeline()
    analyzed: list[dict[str, Any]] = []
    for th in threads_raw:
        bodies = _reddit_fetch_top_comments(th["url"], delay)
        entry: dict[str, Any] = {
            "title": th["title"],
            "url": th["url"],
            "subreddit": th.get("subreddit") or "",
            "top_comment_sample_n": len(bodies),
        }
        if pipe is not None:
            try:
                entry.update(_run_sentiment_on_thread(pipe, th["title"], bodies))
            except Exception as e:  # noqa: BLE001
                entry["sentiment_label"] = "mixed"
                entry["sentiment_positive"] = 0.5
                entry["sentiment_negative"] = 0.5
                entry["sentiment_note"] = f"Scoring error: {e!s}"[:200]
        else:
            entry["sentiment_label"] = "mixed"
            entry["sentiment_positive"] = 0.5
            entry["sentiment_negative"] = 0.5
            entry["sentiment_note"] = "Sentiment model unavailable."
        joined = " ".join(bodies[:3]).strip()
        preview = joined[:320] + ("..." if len(joined) > 320 else "")
        entry["comment_preview"] = preview
        analyzed.append(entry)

    pos_n = sum(1 for a in analyzed if a.get("sentiment_label") == "positive")
    neg_n = sum(1 for a in analyzed if a.get("sentiment_label") == "negative")
    mix_n = sum(1 for a in analyzed if a.get("sentiment_label") == "mixed")
    summary = f"{pos_n} positive tone, {neg_n} negative, {mix_n} mixed/unclear (of {len(analyzed)} sampled threads)."
    err_msg = ""
    if pipe is None and perr:
        err_msg = (
            "Install optional ML deps for Reddit sentiment: "
            "`pip install -r requirements-brand-sentiment.txt` (PyTorch + Transformers). "
            f"Detail: {perr[:160]}"
        )

    return {
        "reddit_threads": analyzed,
        "reddit_sentiment_summary": summary,
        "reddit_sentiment_model": "distilbert-base-uncased-finetuned-sst-2-english",
        "reddit_sentiment_error": err_msg,
    }


def _reddit_subreddit_probe(brand: str, delay: float) -> tuple[bool, str, str | None]:
    for slug in _slugs(brand):
        url = f"https://www.reddit.com/r/{slug}/"
        status, body = _fetch_html(url)
        time.sleep(delay)
        low = body.lower()
        if status == 404 or status is None:
            continue
        if "sorry, there aren't any communities" in low:
            continue
        if "this community has been banned" in low:
            continue
        if status == 200 and ("subscribers" in low or "members" in low):
            return True, f"Subreddit r/{slug} exists.", url
    return False, "", None


def _reddit_probe_legacy_subreddit_only(
    brand: str, delay: float
) -> tuple[bool, str, str | None]:
    """Fallback when Reddit search yields no on-brand threads."""
    ok2, st2, u2 = _reddit_subreddit_probe(brand, delay)
    if ok2:
        return ok2, st2, u2
    return False, "No Reddit search hits for brand variants; try site:reddit.com in a browser.", None


def _linkedin_company_slugs(brand: str, base_url: str) -> list[str]:
    slugs: list[str] = []
    seen: set[str] = set()

    def add(s: str) -> None:
        s = s.strip().strip("/")
        if s and s.lower() not in seen:
            seen.add(s.lower())
            slugs.append(s)

    # Prefer spaced-brand hyphen slugs (e.g. euro-car-parts-ltd-) before compact host slugs.
    for phrase in _search_variants_for_brand(brand, base_url):
        hyp = re.sub(r"[^a-z0-9]+", "-", phrase.lower()).strip("-")
        if hyp:
            add(f"{hyp}-ltd-")
            add(f"{hyp}-ltd")
            add(f"{hyp}-limited")
            add(hyp)
    for s in _slugs(brand):
        add(f"{s}-ltd-")
        add(f"{s}-ltd")
        add(f"{s}-limited")
        add(s)
        nospace = s.replace("-", "")
        if nospace != s:
            add(nospace)
            add(f"{nospace}-ltd")
            add(f"{nospace}-ltd-")
    return slugs[:28]


def _linkedin_probe(
    brand: str, base_url: str, delay: float
) -> tuple[bool, str, str | None]:
    for slug in _linkedin_company_slugs(brand, base_url):
        url = f"https://www.linkedin.com/company/{slug}"
        status: int | None = None
        body = ""
        for attempt in range(3):
            status, body = _fetch_html(url)
            if status != 429:
                break
            time.sleep(max(delay, 0.4) * (2**attempt))
        time.sleep(delay)
        if status == 429:
            continue
        low = body.lower()
        if status in (404, 410):
            continue
        if status is None:
            continue
        if "page not found" in low or "couldn’t find" in low or "couldn't find" in low:
            continue
        if "this page doesn't exist" in low:
            continue
        if status == 200 and (
            ('property="og:url"' in low and "/company/" in low)
            or "linkedin.com/company" in low
            or ('"organization"' in body and "linkedin" in low)
        ):
            return True, f"Company page resolves (slug try: {slug}).", url
        if status == 200 and "linkedin" in low and "/company/" in low:
            return True, f"Company page may exist (slug: {slug}); confirm in browser.", url
    return False, "No LinkedIn /company/ slug matched; verify manually.", None


def scan_brand_platforms(
    brand: str,
    base_url: str,
    *,
    delay: float = 0.25,
    brand_source: str = "derived_hostname",
) -> dict[str, Any]:
    """
    Returns audit_summary-shaped `brand_visibility` dict (four platforms).
    """
    brand = (brand or "").strip() or derive_brand_from_base(base_url)
    rows: list[dict[str, Any]] = []

    ok_wp, st_wp, u_wp = _wikipedia_probe(brand, base_url, delay)
    rows.append(
        {
            "platform": "Wikipedia",
            "present": ok_wp,
            "status": st_wp,
            "url": u_wp,
            "impact": PLATFORM_IMPACT_BLURB["Wikipedia"],
        }
    )

    ok_yt, st_yt, u_yt = _youtube_probe(brand, base_url, delay)
    rows.append(
        {
            "platform": "YouTube",
            "present": ok_yt,
            "status": st_yt,
            "url": u_yt,
            "impact": PLATFORM_IMPACT_BLURB["YouTube"],
        }
    )

    rd_extra = _reddit_scan_search_threads_with_sentiment(brand, base_url, delay)
    r_threads = list(rd_extra.get("reddit_threads") or [])
    if r_threads:
        ok_rd = True
        u_rd = str(r_threads[0].get("url") or "").strip() or None
        st_rd = (
            f"Matched {len(r_threads)} thread(s)—expand for titles, comment samples, "
            "and DistilBERT sentiment (directional only)."
        )
        reddit_row: dict[str, Any] = {
            "platform": "Reddit",
            "present": ok_rd,
            "status": st_rd,
            "url": u_rd,
            "impact": PLATFORM_IMPACT_BLURB["Reddit"],
            **rd_extra,
        }
    else:
        ok_rd, st_rd, u_rd = _reddit_probe_legacy_subreddit_only(brand, delay)
        reddit_row = {
            "platform": "Reddit",
            "present": ok_rd,
            "status": st_rd,
            "url": u_rd,
            "impact": PLATFORM_IMPACT_BLURB["Reddit"],
            "reddit_threads": [],
            "reddit_sentiment_summary": "",
            "reddit_sentiment_model": "distilbert-base-uncased-finetuned-sst-2-english",
            "reddit_sentiment_error": "",
        }
    rows.append(reddit_row)

    ok_li, st_li, u_li = _linkedin_probe(brand, base_url, delay)
    rows.append(
        {
            "platform": "LinkedIn",
            "present": ok_li,
            "status": st_li,
            "url": u_li,
            "impact": PLATFORM_IMPACT_BLURB["LinkedIn"],
        }
    )

    note = (
        "Checks: Wikipedia (multi-query API), YouTube (@handles + search HTML), "
        "Reddit (search.json for up to three on-brand threads; optional DistilBERT SST-2 sentiment on title + "
        "top-scoring comments when requirements-brand-sentiment.txt deps are installed; else subreddit slug fallback), "
        "LinkedIn (expanded /company/ slugs). "
        "Pass an accurate --brand and use the audited domain so host-based guesses "
        "(e.g. autopartsdirect → Auto Parts Direct) apply. Confirm in browser if a site blocks bots."
    )
    return {
        "brand_query": brand,
        "brand_source": brand_source,
        "base_url": base_url,
        "platforms": rows,
        "method_note": note,
    }
