"""Setup wizard helpers (Gemini product lines, etc.)."""

from __future__ import annotations

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field

router = APIRouter(prefix="/api/wizard", tags=["wizard"])


class VerifySiteBody(BaseModel):
    url: str


class VerifySiteResponse(BaseModel):
    canonical_url: str
    hostname: str
    favicon_url: str
    reachable: bool = True
    status_code: int | None = None
    warning: str | None = None
    bot_wall: bool = False
    provider: str | None = None
    browser_verified: bool = False


class ProbeSiteProtectionResponse(BaseModel):
    canonical_url: str
    bot_wall: bool = False
    provider: str | None = None
    status_code: int | None = None


def _http_probe(url: str) -> tuple[int | None, dict[str, str], bytes, str | None]:
    """Quick HTTP GET used by probe + verify endpoints."""
    import httpx

    timeout = httpx.Timeout(8.0, connect=4.0)
    headers = {"User-Agent": "GEO-Audit-Setup/1.0 (site verification)"}
    try:
        with httpx.Client(follow_redirects=True, timeout=timeout) as client:
            resp = client.get(url, headers=headers)
            return (
                resp.status_code,
                {k.lower(): v for k, v in resp.headers.items()},
                resp.content,
                None,
            )
    except httpx.TimeoutException:
        return None, {}, b"", (
            "Could not confirm the site responded in time; we'll still use this URL for the audit."
        )
    except httpx.RequestError as exc:
        return None, {}, b"", f"Could not reach that site ({exc}); we'll still use this URL for the audit."


@router.post("/probe-site-protection", response_model=ProbeSiteProtectionResponse)
def probe_site_protection(body: VerifySiteBody) -> ProbeSiteProtectionResponse:
    """Fast check for Cloudflare / bot walls before the slower browser verification."""
    from browser_fetch import waf_provider
    from geo_setup_llm import normalize_competitor_url

    url = normalize_competitor_url(body.url.strip())
    if not url:
        raise HTTPException(
            400,
            "That URL could not be normalized—add https:// and a valid hostname, then try again.",
        )

    status_code, response_headers, response_body, _ = _http_probe(url)
    provider = waf_provider(status_code, response_headers, response_body)
    return ProbeSiteProtectionResponse(
        canonical_url=url,
        bot_wall=provider is not None,
        provider=provider,
        status_code=status_code,
    )


@router.post("/verify-site", response_model=VerifySiteResponse)
def verify_site(body: VerifySiteBody) -> VerifySiteResponse:
    """Normalize URL, check the host responds, return favicon URL for the wizard preview."""
    from browser_fetch import fetch_once_with_browser, waf_provider
    from domain_suggest import hostname_for_display_url, public_site_favicon_url
    from geo_setup_llm import normalize_competitor_url

    url = normalize_competitor_url(body.url.strip())
    if not url:
        raise HTTPException(
            400,
            "That URL could not be normalized—add https:// and a valid hostname, then try again.",
        )

    host = hostname_for_display_url(url)
    if not host:
        raise HTTPException(400, "Could not parse a hostname from that URL.")

    favicon = public_site_favicon_url(host)
    status_code, response_headers, response_body, warning = _http_probe(url)
    provider = waf_provider(status_code, response_headers, response_body)
    bot_wall = provider is not None
    browser_verified = False

    if status_code is not None and status_code >= 400:
        if bot_wall:
            pw_status, pw_body, _ = fetch_once_with_browser(url, timeout_ms=55000, max_attempts=3)
            if pw_status is not None and pw_status < 400:
                status_code = pw_status
                browser_verified = True
                warning = (
                    "Site uses Cloudflare protection; verified with a browser fetch."
                )
            elif not pw_body:
                warning = (
                    "Cloudflare protection detected. Browser verification could not run "
                    "(locally: run `.venv/bin/playwright install chromium` and restart the dev server)."
                )
            else:
                warning = (
                    "Cloudflare protection detected. Our servers couldn't complete the browser "
                    "check (common from cloud datacenters). You can continue—the audit will still "
                    "try a browser crawl, but some pages may be missing."
                )
        else:
            warning = (
                f"Site responded with HTTP {status_code}; we'll still use this URL for the audit."
            )

    return VerifySiteResponse(
        canonical_url=url,
        hostname=host,
        favicon_url=favicon,
        reachable=True,
        status_code=status_code,
        warning=warning,
        bot_wall=bot_wall,
        provider=provider,
        browser_verified=browser_verified,
    )


class SuggestProductsBody(BaseModel):
    brand_website: str
    market_country: str = ""
    market_country_code: str = ""


class ProductServiceRow(BaseModel):
    product_or_service: str
    prompts: list[str] = Field(default_factory=list)


class SuggestProductsResponse(BaseModel):
    rows: list[ProductServiceRow]


@router.post("/suggest-products", response_model=SuggestProductsResponse)
def suggest_products(body: SuggestProductsBody) -> SuggestProductsResponse:
    from geo_setup_llm import normalize_competitor_url, suggest_products_and_services

    url = normalize_competitor_url(body.brand_website.strip())
    if not url:
        raise HTTPException(
            400,
            "Add your brand website on step 1 before Gemini can suggest product or service lines.",
        )
    try:
        rows = suggest_products_and_services(
            url,
            market_country=body.market_country.strip(),
            market_country_code=body.market_country_code.strip(),
        )
    except Exception as exc:
        raise HTTPException(502, str(exc)) from exc
    if not rows:
        raise HTTPException(502, "Gemini returned no product or service lines.")
    return SuggestProductsResponse(rows=[ProductServiceRow.model_validate(r) for r in rows])


class SuggestPromptsForProductsBody(BaseModel):
    brand_website: str
    products: list[str] = Field(..., min_length=1, max_length=12)
    market_country: str = ""
    market_country_code: str = ""


@router.post("/suggest-prompts-for-products", response_model=SuggestProductsResponse)
def suggest_prompts_for_products(body: SuggestPromptsForProductsBody) -> SuggestProductsResponse:
    """Gemini prompts for user-typed product lines that have no prompts yet."""
    from geo_setup_llm import normalize_competitor_url, suggest_prompts_for_product_lines

    url = normalize_competitor_url(body.brand_website.strip())
    if not url:
        raise HTTPException(400, "Brand website is required.")
    names = [str(p).strip() for p in body.products if str(p).strip()]
    if not names:
        raise HTTPException(400, "Provide at least one product or service name.")
    try:
        rows = suggest_prompts_for_product_lines(
            url,
            names,
            market_country=body.market_country.strip(),
            market_country_code=body.market_country_code.strip(),
        )
    except Exception as exc:
        raise HTTPException(502, str(exc)) from exc
    if not rows:
        raise HTTPException(502, "Gemini returned no prompts for those product lines.")
    return SuggestProductsResponse(rows=[ProductServiceRow.model_validate(r) for r in rows])


class SuggestCompetitorsBody(BaseModel):
    brand_website: str
    products_and_services: list[str] = Field(default_factory=list)
    market_country: str = ""
    market_country_code: str = ""


class CompetitorRow(BaseModel):
    competitor_brand: str
    competitor_website: str
    favicon_url: str = ""


class SuggestCompetitorsResponse(BaseModel):
    rows: list[CompetitorRow]


def _competitor_rows_from_gemini(
    found: list[dict],
) -> list[CompetitorRow]:
    from domain_suggest import hostname_for_display_url, public_site_favicon_url
    from geo_setup_llm import normalize_competitor_url

    rows: list[CompetitorRow] = []
    for r in found:
        if not isinstance(r, dict):
            continue
        nu = normalize_competitor_url(str(r.get("competitor_website") or ""))
        if not nu:
            continue
        host = hostname_for_display_url(nu)
        rows.append(
            CompetitorRow(
                competitor_brand=str(r.get("competitor_brand") or "").strip(),
                competitor_website=nu,
                favicon_url=public_site_favicon_url(host) if host else "",
            )
        )
    return rows


@router.post("/suggest-competitors", response_model=SuggestCompetitorsResponse)
def suggest_competitors_endpoint(body: SuggestCompetitorsBody) -> SuggestCompetitorsResponse:
    from geo_setup_llm import normalize_competitor_url, suggest_competitors

    url = normalize_competitor_url(body.brand_website.strip())
    if not url:
        raise HTTPException(
            400,
            "Brand website missing—complete step 1 and choose Continue.",
        )
    products = [str(p).strip() for p in body.products_and_services if str(p).strip()]
    if not products:
        raise HTTPException(400, "Select products or services in step 3 first.")

    try:
        found = suggest_competitors(
            url,
            products,
            market_country=body.market_country.strip(),
            market_country_code=body.market_country_code.strip(),
        )
    except Exception as exc:
        raise HTTPException(502, str(exc)) from exc

    return SuggestCompetitorsResponse(rows=_competitor_rows_from_gemini(found))


@router.post("/normalize-competitor-url")
def normalize_competitor_url_endpoint(body: VerifySiteBody) -> dict[str, str]:
    from domain_suggest import hostname_for_display_url, public_site_favicon_url
    from geo_setup_llm import normalize_competitor_url

    url = normalize_competitor_url(body.url.strip())
    if not url:
        raise HTTPException(
            400,
            "That URL could not be normalized—add https:// and a valid hostname.",
        )
    host = hostname_for_display_url(url)
    return {
        "canonical_url": url,
        "favicon_url": public_site_favicon_url(host) if host else "",
    }
