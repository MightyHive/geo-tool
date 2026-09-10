"""Search Console user OAuth and property selection (session-backed)."""

from __future__ import annotations

import logging
import os
import secrets
import urllib.parse
import csv
from pathlib import Path
from typing import Any

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import RedirectResponse
from pydantic import BaseModel, Field

from api.auth_config import resolve_web_public_origin
from api.ga4_config import load_ga4_oauth_config

log = logging.getLogger(__name__)

router = APIRouter(prefix="/api/gsc", tags=["gsc"])

SESSION_CREDS_KEY = "gsc_user_creds_dict"
SESSION_SITE_KEY = "gsc_selected_site_url"

# Combined with openid set the same way as GA4 so one Web client can be reused.
GSC_OAUTH_SCOPES: tuple[str, ...] = (
    "openid",
    "https://www.googleapis.com/auth/userinfo.email",
    "https://www.googleapis.com/auth/userinfo.profile",
    "https://www.googleapis.com/auth/webmasters.readonly",
)


def gsc_redirect_uri(request: Request) -> str:
    explicit = (os.environ.get("GSC_OAUTH_REDIRECT_URI") or "").strip()
    if explicit:
        return explicit
    origin = resolve_web_public_origin(request).rstrip("/")
    return f"{origin}/api/gsc/callback"


def _safe_return_to(raw: str | None) -> str | None:
    if not raw:
        return None
    path = str(raw).strip()
    if not path.startswith("/") or path.startswith("//") or "\\" in path:
        return None
    if any(c in path for c in ("\n", "\r", "\0")):
        return None
    return path


def _redirect(origin: str, return_to: str | None, **params: str) -> RedirectResponse:
    target = _safe_return_to(return_to) or "/audit/new"
    separator = "&" if "?" in target else "?"
    query = urllib.parse.urlencode(params)
    return RedirectResponse(f"{origin}{target}{separator}{query}", status_code=302)


def _credentials(request: Request, *, refresh: bool = True):
    raw = request.session.get(SESSION_CREDS_KEY)
    if not isinstance(raw, dict) or not raw.get("refresh_token"):
        raise HTTPException(status_code=401, detail="Connect Search Console first.")
    import ga4_oauth as g4o

    creds = g4o.credentials_from_dict(raw)
    if refresh:
        creds = g4o.ensure_fresh_credentials(creds)
        request.session[SESSION_CREDS_KEY] = g4o.credentials_to_dict(creds)
    return creds


def _list_sites(request: Request) -> list[dict[str, str]]:
    from googleapiclient.discovery import build

    creds = _credentials(request)
    service = build("searchconsole", "v1", credentials=creds, cache_discovery=False)
    response = service.sites().list().execute()
    rows: list[dict[str, str]] = []
    for entry in response.get("siteEntry", []) or []:
        site_url = str(entry.get("siteUrl") or "").strip()
        permission = str(entry.get("permissionLevel") or "").strip()
        if site_url and permission != "siteUnverifiedUser":
            rows.append({"site_url": site_url, "permission_level": permission})
    return sorted(rows, key=lambda row: row["site_url"].lower())


def export_search_analytics_daily(
    request: Request,
    *,
    site_url: str,
    start_date: str,
    end_date: str,
    output_path: Path,
) -> Path:
    """Export daily Search Console metrics with pagination."""
    from googleapiclient.discovery import build

    creds = _credentials(request)
    service = build("searchconsole", "v1", credentials=creds, cache_discovery=False)
    rows: list[dict[str, Any]] = []
    start_row = 0
    row_limit = 25_000
    while True:
        body = {
            "startDate": start_date,
            "endDate": end_date,
            "dimensions": ["date"],
            "rowLimit": row_limit,
            "startRow": start_row,
            "dataState": "final",
        }
        response = (
            service.searchanalytics()
            .query(siteUrl=site_url, body=body)
            .execute()
        )
        batch = response.get("rows", []) or []
        for row in batch:
            keys = row.get("keys") or []
            if not keys:
                continue
            rows.append(
                {
                    "date": str(keys[0]),
                    "clicks": float(row.get("clicks") or 0),
                    "impressions": float(row.get("impressions") or 0),
                    "ctr": float(row.get("ctr") or 0),
                    "position": float(row.get("position") or 0),
                }
            )
        if len(batch) < row_limit:
            break
        start_row += row_limit

    output_path.parent.mkdir(parents=True, exist_ok=True)
    with output_path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(
            handle,
            fieldnames=["date", "clicks", "impressions", "ctr", "position"],
        )
        writer.writeheader()
        writer.writerows(rows)
    return output_path


class GscSelection(BaseModel):
    site_url: str = Field(..., min_length=1, description="Search Console property URI")


@router.get("/status")
def gsc_status(request: Request) -> dict[str, Any]:
    creds = request.session.get(SESSION_CREDS_KEY)
    connected = bool(isinstance(creds, dict) and creds.get("refresh_token"))
    configured = load_ga4_oauth_config() is not None
    return {
        "configured": configured,
        "connected": connected,
        "site_url": request.session.get(SESSION_SITE_KEY),
        "scopes": list(GSC_OAUTH_SCOPES),
        "login_path": "/api/gsc/login",
        "redirect_uri": gsc_redirect_uri(request),
        "error": request.session.pop("gsc_oauth_error", None),
    }


@router.get("/login")
def gsc_login(request: Request, return_to: str = "") -> RedirectResponse:
    """Start Search Console OAuth using the same web client as GA4."""
    cfg = load_ga4_oauth_config()  # shared web client id/secret
    if cfg is None:
        raise HTTPException(
            status_code=503,
            detail="OAuth client not configured (reuse [ga4_oauth] / [auth] client).",
        )
    try:
        import ga4_oauth as g4o

        redirect_uri = gsc_redirect_uri(request)
        flow = g4o.build_flow(
            client_id=cfg.client_id,
            client_secret=cfg.client_secret,
            redirect_uri=redirect_uri,
            scopes=list(GSC_OAUTH_SCOPES),
        )
        state = secrets.token_urlsafe(32)
        auth_url, _ = flow.authorization_url(
            access_type="offline",
            include_granted_scopes="true",
            prompt="consent",
            state=state,
        )
        request.session["gsc_oauth_state"] = state
        request.session["gsc_oauth_redirect_uri"] = redirect_uri
        request.session["gsc_oauth_return_to"] = _safe_return_to(return_to)
        return RedirectResponse(str(auth_url), status_code=302)
    except Exception as exc:  # noqa: BLE001
        log.exception("GSC login failed")
        raise HTTPException(status_code=500, detail=str(exc)) from exc


@router.get("/callback")
def gsc_callback(request: Request, code: str | None = None, state: str | None = None) -> RedirectResponse:
    origin = resolve_web_public_origin(request).rstrip("/")
    return_to = request.session.get("gsc_oauth_return_to")
    if not code:
        return _redirect(origin, return_to, gsc_error="missing_code")
    expected = request.session.get("gsc_oauth_state")
    if not expected or not state or not secrets.compare_digest(str(expected), str(state)):
        return _redirect(origin, return_to, gsc_error="state_mismatch")
    cfg = load_ga4_oauth_config()
    if cfg is None:
        return _redirect(origin, return_to, gsc_error="not_configured")
    try:
        import ga4_oauth as g4o

        redirect_uri = request.session.get("gsc_oauth_redirect_uri") or gsc_redirect_uri(request)
        flow = g4o.build_flow(
            client_id=cfg.client_id,
            client_secret=cfg.client_secret,
            redirect_uri=redirect_uri,
            scopes=list(GSC_OAUTH_SCOPES),
        )
        previous_relax = os.environ.get("OAUTHLIB_RELAX_TOKEN_SCOPE")
        os.environ["OAUTHLIB_RELAX_TOKEN_SCOPE"] = "1"
        try:
            flow.fetch_token(code=code)
        finally:
            if previous_relax is None:
                os.environ.pop("OAUTHLIB_RELAX_TOKEN_SCOPE", None)
            else:
                os.environ["OAUTHLIB_RELAX_TOKEN_SCOPE"] = previous_relax
        creds = flow.credentials
        if not creds.refresh_token:
            raise RuntimeError(
                "Google did not return a refresh token. Remove the app from your "
                "Google Account permissions, then connect again."
            )
        request.session[SESSION_CREDS_KEY] = g4o.credentials_to_dict(creds)
        for key in ("gsc_oauth_state", "gsc_oauth_redirect_uri", "gsc_oauth_return_to"):
            request.session.pop(key, None)
        return _redirect(origin, return_to, gsc_connected="1")
    except Exception as exc:  # noqa: BLE001
        log.exception("GSC callback failed: %s", exc)
        request.session["gsc_oauth_error"] = str(exc)
        return _redirect(origin, return_to, gsc_error="exchange_failed")


@router.put("/selection")
def gsc_selection(body: GscSelection, request: Request) -> dict[str, str]:
    _credentials(request)
    site_url = body.site_url.strip()
    if not site_url:
        raise HTTPException(status_code=400, detail="Select a Search Console property.")
    request.session[SESSION_SITE_KEY] = site_url
    return {"site_url": site_url}


@router.post("/disconnect")
def gsc_disconnect(request: Request) -> dict[str, bool]:
    request.session.pop(SESSION_CREDS_KEY, None)
    request.session.pop(SESSION_SITE_KEY, None)
    return {"ok": True}


@router.get("/sites")
def gsc_list_sites(request: Request) -> dict[str, Any]:
    """List verified Search Console properties available to the connected account."""
    try:
        return {"sites": _list_sites(request)}
    except HTTPException:
        raise
    except Exception as exc:  # noqa: BLE001
        log.exception("Could not list Search Console properties")
        message = str(exc)
        if "accessNotConfigured" in message or "SERVICE_DISABLED" in message:
            raise HTTPException(
                status_code=503,
                detail="Enable the Google Search Console API in geo-tool-emea-ds, then retry.",
            ) from exc
        raise HTTPException(
            status_code=502,
            detail=f"Could not list Search Console properties: {message}",
        ) from exc
