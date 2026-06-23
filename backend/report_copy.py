"""
Client-facing rewrites and prioritisation for GEO report copy.
Normalises raw crawl/audit strings before they appear in HTML.
"""

from __future__ import annotations

from typing import Iterable

# Longer needles first so more specific phrases win.
_REWRITE_SUBSTRINGS: list[tuple[str, str]] = [
    (
        "merged robots.txt suggestion is in this audit folder",
        "Ask your developer to update the site’s crawler access rules in robots.txt so search engines "
        "and AI tools can read the public pages you want cited.",
    ),
    (
        "improve discovery: live llms.txt, reachable sitemap",
        "Publish an AI guide file (llms.txt) and make sure your sitemap lists the important pages you "
        "want search engines and AI tools to find.",
    ),
    (
        "no live llms.txt at origin",
        "Publish a short AI guide file (llms.txt) that points AI tools to your most important product, "
        "service, support, company, and policy pages.",
    ),
    (
        "consider blocking bytespider",
        "Decide whether to allow ByteDance’s AI training crawler, Bytespider. This is a data-use policy "
        "choice, not a core AI search visibility fix.",
    ),
    (
        "core web vitals and ttfb are not measured",
        "Run a page speed and usability check to confirm whether loading speed is affecting users or crawlers.",
    ),
    (
        "news, reviews, directories, and google business profile corroboration are not fully automated",
        "Manually verify key third-party brand sources such as reviews, directories, news mentions, and Google Business Profile.",
    ),
    (
        "visit quality is mostly manual",
        "Set up measurement for AI-search visit quality, including conversions, engaged sessions, and query-to-page reporting.",
    ),
    (
        "no schema freshness signals detected",
        "Add accurate update dates to important structured data where freshness matters.",
    ),
    (
        "few machine-readable signals were extractable",
        "Check whether important page content and structured data are visible to crawlers without relying on JavaScript.",
    ),
    (
        "validate canonical and noindex behavior",
        "Check key templates to make sure important pages are listable in search and point to the correct main URL.",
    ),
    (
        "ensure robots.txt declares a live sitemap url",
        "Reference the live sitemap in robots.txt so crawlers can find priority pages more easily.",
    ),
    (
        "run citation footprint checks",
        "Check whether the brand appears in ChatGPT, Perplexity, Google AI Overviews, and Copilot for priority queries.",
    ),
]


def client_friendly_text(text: str) -> str:
    """Rewrite known audit phrases; otherwise normalise whitespace."""
    if not text or not str(text).strip():
        return ""
    normalised = " ".join(str(text).split())
    low = normalised.lower()
    for needle, replacement in _REWRITE_SUBSTRINGS:
        if needle in low:
            return replacement
    return normalised


def client_friendly_finding(text: str, section: str = "general") -> str:
    """Alias for display contexts (insights, findings, competitor notes). Section reserved for future tuning."""
    _ = section
    return client_friendly_text(text)


def is_manual_caveat(text: str) -> bool:
    t = (text or "").lower()
    return any(
        x in t
        for x in (
            "not fully automated",
            "manual check",
            "validate manually",
            "mostly manual",
            "not measured in this automated",
            "not measured",
            "manual verification",
            "manual spot-check",
            "manual checks",
            "require manual",
            "still require manual",
        )
    )


def is_measurement_only(text: str) -> bool:
    t = (text or "").lower()
    return any(
        x in t
        for x in (
            "not measured",
            "speed tool",
            "core web vitals",
            "ttfb",
            "lighthouse",
            "crux",
        )
    )


def is_policy_only_item(text: str) -> bool:
    t = (text or "").lower()
    return any(
        x in t
        for x in (
            "bytespider",
            "bytedance",
            "byte dance",
            "training crawler",
            "ccbot",
            "cohere-ai",
            "anthropic-ai",
            "meta-externalagent",
            "policy choice, not a core",
        )
    )


def action_dedupe_bucket(text: str) -> str:
    """Bucket for collapsing duplicate discovery / robots recommendations."""
    t = " ".join((text or "").lower().split())
    if is_policy_only_item(text):
        return "policy_training"
    if "ai guide file" in t and "sitemap" in t:
        return "site_discovery"
    if "llms.txt" in t or "ai guide file" in t:
        return "site_discovery"
    if "sitemap" in t and ("robots" in t or "robots.txt" in t):
        return "site_discovery"
    if any(
        x in t
        for x in (
            "robots.txt",
            "crawler access",
            "crawler rules",
            "search engines and ai tools can read",
        )
    ):
        return "robots_access"
    if any(
        x in t
        for x in (
            "structured data",
            "json-ld",
            "sameas",
            "organization structured",
            "official profile",
            "brand data",
        )
    ):
        return "structured_brand"
    if any(x in t for x in ("answer summar", "passage", "cite-worthy", "clearer answer")):
        return "content_answers"
    if any(
        x in t
        for x in (
            "machine-readable signals",
            "without relying on javascript",
            "js-only",
            "client-rendered",
        )
    ):
        return "js_rendering"
    if any(x in t for x in ("core web vitals", "ttfb", "page speed", "speed tool")):
        return "performance_check"
    if any(x in t for x in ("visit quality", "engaged sessions", "query-to-page")):
        return "measurement_visits"
    if "citation" in t or ("chatgpt" in t and "perplexity" in t):
        return "citation_checks"
    if "schema freshness" in t or ("update dates" in t and "structured" in t):
        return "schema_freshness"
    if any(x in t for x in ("canonical", "noindex", "listable in search", "main url")):
        return "canonical_index"
    if any(x in t for x in ("content clusters", "original research", "first-party data", "case studies")):
        return "strategic_content"
    if "reddit" in t and ("footprint" in t or "community" in t):
        return "strategic_community"
    return "general"


def classify_action_horizon_and_rank(text: str) -> tuple[str, int]:
    """
    horizon: quick | medium | strategic | policy
    rank: lower = earlier in a phase list

    Strategic is reserved for long-horizon **content**, **brand / authority uplift**,
    and **citability** programmes—not one-off technical fixes (those stay quick/medium).
    """
    t = " ".join((text or "").lower().split())
    if is_policy_only_item(text):
        return "policy", 100
    if "canonical" in t or "noindex" in t or ("listable in search" in t and "main url" in t):
        return "quick", 12
    if "sitemap" in t and ("robots" in t or "robots.txt" in t):
        return "quick", 14
    if "merged robots" in t or ("crawler access" in t and "developer" in t):
        return "quick", 6
    if "robots.txt" in t and ("crawler" in t or "search engines" in t):
        return "quick", 8
    if "llms.txt" in t or "ai guide file" in t:
        return "quick", 22
    if any(
        x in t
        for x in (
            "json-ld",
            "structured data",
            "sameas",
            "organization structured",
            "official profiles",
        )
    ):
        return "quick", 20
    if any(
        x in t
        for x in (
            "machine-readable signals",
            "without relying on javascript",
            "js-only",
        )
    ):
        return "medium", 18
    # Brand / citation footprint checks → strategic (ongoing programmes, not a single fix)
    if "citation footprint" in t or (
        "brand appears" in t
        and ("chatgpt" in t or "perplexity" in t or "copilot" in t or "overviews" in t)
    ):
        return "strategic", 46
    if "schema freshness" in t or ("update dates" in t and "structured data" in t):
        return "medium", 32
    # Strategic: research-led content, authority, originality, governance (90+ day motion)
    _strategic_research_authority = (
        "content clusters",
        "original research",
        "first-party data",
        "first-party",
        "case stud",
        "expert commentary",
        "unique examples",
        "original information gain",
        "thought leadership",
        "editorial",
        "content governance",
        "methodology",
        "whitepaper",
        "research paper",
        "digital pr",
        "hreflang",
        "youtube program",
        "community building",
        "wikidata",
        "wikipedia",
    )
    if any(x in t for x in _strategic_research_authority):
        return "strategic", 34
    if any(
        x in t
        for x in (
            "source transparency",
            "governance",
            "disclosures",
            "corrections",
            "review process",
        )
    ):
        return "strategic", 36
    # Strategic: citability & answer surfaces (programme-scale, not a single template tweak)
    _strategic_citability = (
        "passage patterns",
        "query shapes",
        "definitions",
        "direct answers",
        "troubleshooting",
        "pricing",
        "comparisons",
        "faq",
        "takeaways",
        "cite-worthy",
        "answer summar",
        "passage-level",
        "citability",
        "citable",
        "eeat",
        "e-e-a-t",
        "authorship",
    )
    if any(x in t for x in _strategic_citability):
        return "strategic", 40
    if "reddit" in t and any(x in t for x in ("community", "footprint", "forum")):
        return "strategic", 48
    if "visit quality" in t or "engaged sessions" in t:
        return "medium", 42
    if any(x in t for x in ("core web vitals", "ttfb", "speed tool", "page speed")):
        return "medium", 48
    if any(x in t for x in ("not fully automated", "validate manually", "manual verify")):
        return "medium", 55
    return "medium", 72


_STRATEGIC_PLAN_BACKFILL: list[str] = [
    "Stand up a research-led content programme: publish repeatable methodology notes, primary data, and expert Q&A so priority topics have cite-ready answers models can quote with confidence.",
    "Treat citability as a roadmap: map AI query shapes (definitions, comparisons, troubleshooting, pricing) to owned pages, add passage patterns (clear headings, tables, FAQs, takeaways), and set refresh cadences tied to product or regulatory change.",
    "Invest in brand corroboration beyond your domain: earn sustained, verifiable mentions in industry press, standards bodies, professional networks, and communities so entity checks consistently reinforce who you are.",
    "Build an editorial governance layer: author bios, sourcing rules, reviewer sign-off, corrections, and disclosures that scale to new templates without diluting E-E-A-T.",
    "Run a long-cycle originality push: case studies with named metrics, first-party experiments, and evidence-led perspectives that competitors cannot copy quickly.",
]


def _backfill_strategic_column(items: list[str], *, limit: int = 5) -> list[str]:
    """Ensure the strategic column stays full of long-horizon content/brand/citability motion."""
    out = [x.strip() for x in items if x.strip()]
    if len(out) >= limit:
        return out[:limit]

    def _sig(s: str) -> str:
        return " ".join(s.lower().split())[:52]

    seen = {_sig(x) for x in out}
    for line in _STRATEGIC_PLAN_BACKFILL:
        if len(out) >= limit:
            break
        if line in out:
            continue
        sig = _sig(line)
        if sig in seen:
            continue
        if any(sig == _sig(o) for o in out):
            continue
        out.append(line)
        seen.add(sig)
    return out[:limit]


def prepare_report_priorities(raw: Iterable[str]) -> tuple[list[str], list[str], list[str], list[str], list[str]]:
    """
    From consolidated improvement lines, produce:
      quick, medium, strategic, policy_notes (each capped at 5),
      narrative_ordered (for executive summary + key-findings ordering).
    """
    best: dict[str, tuple[int, str]] = {}
    for item in raw:
        rw = client_friendly_text((item or "").strip())
        if not rw:
            continue
        _h, rnk = classify_action_horizon_and_rank(rw)
        bucket = action_dedupe_bucket(rw)
        cur = best.get(bucket)
        if cur is None or rnk < cur[0]:
            best[bucket] = (rnk, rw)

    def pick(horizon_key: str, limit: int) -> list[str]:
        cands: list[tuple[int, str]] = []
        for rnk, rw in best.values():
            h, _ = classify_action_horizon_and_rank(rw)
            if h == horizon_key:
                cands.append((rnk, rw))
        return [rw for _, rw in sorted(cands, key=lambda x: (x[0], x[1].lower()))[:limit]]

    quick = pick("quick", 5)
    medium = pick("medium", 5)
    strategic = _backfill_strategic_column(pick("strategic", 5), limit=5)
    policy = pick("policy", 5)

    flat = [rw for _, rw in sorted(best.values(), key=lambda x: (x[0], x[1].lower()))]
    narrative_ordered = sorted(
        flat,
        key=lambda x: (
            is_manual_caveat(x),
            is_policy_only_item(x),
            classify_action_horizon_and_rank(x)[1],
            x.lower(),
        ),
    )
    return quick, medium, strategic, policy, narrative_ordered


def priorities_for_executive(priorities: list[str]) -> list[str]:
    """Prefer non–manual-caveat lines for the opening executive narrative."""
    pref = [p for p in priorities if not is_manual_caveat(p)]
    return pref if pref else list(priorities)
