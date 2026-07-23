"""GA4 user OAuth for the TypeScript wizard (session-backed)."""

from __future__ import annotations

import base64
import hashlib
import hmac
import json
import logging
import time
import urllib.parse
from typing import Any

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import RedirectResponse
from pydantic import BaseModel, Field, field_validator

from api.auth import cookie_secret
from api.auth_config import resolve_web_public_origin
from api.ga4_config import load_ga4_oauth_config

log = logging.getLogger(__name__)

SESSION_CREDS_KEY = "ga4_user_creds_dict"
SESSION_PROPERTY_KEY = "ga4_selected_property_id"
SESSION_ACCOUNT_KEY = "ga4_selected_account_id"
SESSION_AI_CHANNELS_KEY = "ga4_ai_channel_names"
SESSION_CONVERSION_EVENT_KEY = "ga4_conversion_event_name"


def _session_conversion_events(request: Request) -> list[dict[str, str]]:
    from api.conversion_events import conversion_events_as_dicts, parse_conversion_events

    raw = str(request.session.get(SESSION_CONVERSION_EVENT_KEY) or "purchase")
    try:
        return conversion_events_as_dicts(parse_conversion_events(raw))
    except Exception:  # noqa: BLE001
        return [{"event": "purchase", "label": "purchase"}]


def resolve_ga4_for_audit_run(
    request: Request,
    *,
    ga4_property_id: str | None = None,
    ga4_ai_channels: str | None = None,
) -> tuple[str | None, str | None, "Path | None"]:
    """
    Property + AI channel labels from the run request body, then wizard session.
    When a property is set and the user connected GA4 OAuth, return a temp ADC JSON path
    for ``GOOGLE_APPLICATION_CREDENTIALS`` in the report subprocess.
    """
    from pathlib import Path

    prop = (
        (ga4_property_id or "").strip()
        or str(request.session.get(SESSION_PROPERTY_KEY) or "").strip()
        or None
    )
    if ga4_ai_channels is None:
        ch_raw = request.session.get(SESSION_AI_CHANNELS_KEY)
        channels = str(ch_raw).strip() if ch_raw is not None else None
    else:
        channels = str(ga4_ai_channels).strip() or None

    cred_path: Path | None = None
    if prop:
        creds_dict = request.session.get(SESSION_CREDS_KEY)
        if isinstance(creds_dict, dict) and creds_dict.get("refresh_token"):
            try:
                import ga4_oauth as g4o

                creds = g4o.credentials_from_dict(creds_dict)
                cred_path = g4o.write_temp_application_default_user_json(creds)
            except Exception as exc:
                log.warning("GA4 temp credentials for audit run failed: %s", exc)
                cred_path = None

    return prop, channels, cred_path


def _safe_return_to(raw: str | None) -> str | None:
    """Allow only same-origin relative paths (no scheme / protocol-relative)."""
    if not raw:
        return None
    path = str(raw).strip()
    if not path.startswith("/") or path.startswith("//") or "\\" in path:
        return None
    if any(c in path for c in ("\n", "\r", "\0")):
        return None
    return path


def _sign_ga4_state(
    secret: str,
    *,
    wizard_step: int = 2,
    wiz_ga4_after_yes: bool = False,
    return_to: str | None = None,
    ttl_sec: int = 900,
) -> str:
    payload: dict[str, Any] = {
        "e": int(time.time()) + ttl_sec,
        "v": "new_audit",
        "s": int(wizard_step),
    }
    if wiz_ga4_after_yes:
        payload["w"] = 1
    safe = _safe_return_to(return_to)
    if safe:
        payload["r"] = safe
    raw = json.dumps(payload, separators=(",", ":")).encode("utf-8")
    body = base64.urlsafe_b64encode(raw).decode("ascii").rstrip("=")
    sig = hmac.new(secret.encode("utf-8"), body.encode("ascii"), hashlib.sha256).hexdigest()[:32]
    return f"g4.{body}.{sig}"


def _parse_ga4_state(state: str, secret: str) -> dict[str, Any] | None:
    if not state.startswith("g4."):
        return None
    try:
        _, body, sig = state.split(".", 2)
        expect = hmac.new(secret.encode("utf-8"), body.encode("ascii"), hashlib.sha256).hexdigest()[:32]
        if not hmac.compare_digest(expect, sig):
            return None
        pad = "=" * (-len(body) % 4)
        data = json.loads(base64.urlsafe_b64decode((body + pad).encode("ascii")))
        if int(data.get("e", 0)) < int(time.time()):
            return None
        if data.get("v") != "new_audit":
            return None
        return data
    except Exception:
        return None


def _callback_redirect(origin: str, state_data: dict[str, Any] | None, *, query: str) -> str:
    """Redirect to return_to (AI Impact / report) or wizard step 2."""
    ret = _safe_return_to((state_data or {}).get("r") if state_data else None)
    if ret:
        sep = "&" if "?" in ret else "?"
        return f"{origin}{ret}{sep}{query}"
    return f"{origin}/audit/new?step=2&{query}"


def _web_origin(request: Request) -> str:
    return resolve_web_public_origin(request)


class Ga4PropertyBody(BaseModel):
    property_id: str = Field(..., min_length=1)
    account_id: str = ""
    ai_channel_names: str = ""
    conversion_event_name: str | None = Field(default=None, min_length=1, max_length=500)

    @field_validator("conversion_event_name")
    @classmethod
    def _validate_conversion_event_name(cls, value: str | None) -> str | None:
        if value is None:
            return None
        from api.conversion_events import (
            ConversionEventParseError,
            normalize_conversion_event_spec,
        )

        try:
            return normalize_conversion_event_spec(value)
        except ConversionEventParseError as exc:
            raise ValueError(str(exc)) from exc


def ga4_status_payload(request: Request) -> dict[str, Any]:
    from api.ga4_config import ga4_redirect_uri_for_request

    cfg = load_ga4_oauth_config()
    web_origin = _web_origin(request)
    redirect_hint = ga4_redirect_uri_for_request(web_origin)
    creds = request.session.get(SESSION_CREDS_KEY)
    connected = isinstance(creds, dict) and bool(creds.get("refresh_token"))
    properties: list[dict[str, str]] = []
    accounts: list[dict[str, str]] = []
    error: str | None = request.session.pop("ga4_oauth_error", None)

    if connected and cfg is not None:
        try:
            import ga4_oauth as g4o

            creds_obj = g4o.credentials_from_dict(creds)
            properties = g4o.list_ga4_properties(creds_obj)
            accounts = g4o.accounts_from_property_rows(properties)
        except Exception as exc:
            log.exception("GA4 list properties failed: %s", exc)
            error = str(exc)
            properties = []
            accounts = []

    selected_property_id = str(request.session.get(SESSION_PROPERTY_KEY) or "")
    selected_account_id = str(request.session.get(SESSION_ACCOUNT_KEY) or "")
    if not selected_account_id and selected_property_id and properties:
        for row in properties:
            if str(row.get("id") or "") == selected_property_id:
                selected_account_id = str(row.get("account_id") or "")
                break

    return {
        "configured": cfg is not None,
        "connected": connected,
        "redirect_uri": cfg.redirect_uri if cfg else redirect_hint,
        "accounts": accounts,
        "properties": properties,
        "selected_account_id": selected_account_id,
        "selected_property_id": selected_property_id,
        "ai_channel_names": str(request.session.get(SESSION_AI_CHANNELS_KEY) or ""),
        "conversion_event_name": str(
            request.session.get(SESSION_CONVERSION_EVENT_KEY) or "purchase"
        ),
        "conversion_events": _session_conversion_events(request),
        "error": error,
    }


def create_ga4_router() -> APIRouter:
    router = APIRouter(prefix="/api/ga4", tags=["ga4"])

    @router.get("/status")
    def ga4_status(request: Request) -> dict[str, Any]:
        return ga4_status_payload(request)

    @router.get("/login")
    def ga4_login(
        request: Request,
        wizard_step: int = 2,
        after_yes: str = "1",
        return_to: str = "",
    ) -> RedirectResponse:
        cfg = load_ga4_oauth_config()
        if cfg is None:
            raise HTTPException(
                503,
                "GA4 OAuth is not configured. Add [ga4_oauth] or [auth] in secrets.toml.",
            )
        secret = cookie_secret()
        if not secret:
            raise HTTPException(503, "Missing auth cookie_secret for GA4 OAuth state signing.")

        try:
            import ga4_oauth as g4o

            state = _sign_ga4_state(
                secret,
                wizard_step=wizard_step,
                wiz_ga4_after_yes=after_yes not in ("0", "false", "False"),
                return_to=return_to or None,
            )
            flow = g4o.build_flow(cfg.client_id, cfg.client_secret, cfg.redirect_uri)
            url = g4o.authorization_url(flow, state=state)
        except Exception as exc:
            log.exception("GA4 login start failed: %s", exc)
            raise HTTPException(500, f"Could not start GA4 OAuth: {exc}") from exc

        return RedirectResponse(url, status_code=302)

    @router.get("/callback")
    def ga4_callback(request: Request, code: str = "", state: str = "") -> RedirectResponse:
        origin = _web_origin(request)
        secret = cookie_secret()
        state_data = _parse_ga4_state(state, secret) if state else None

        if not code or not state:
            return RedirectResponse(
                _callback_redirect(origin, state_data, query="ga4_error=missing"),
                status_code=302,
            )

        if not state_data:
            return RedirectResponse(
                _callback_redirect(origin, None, query="ga4_error=state"),
                status_code=302,
            )

        cfg = load_ga4_oauth_config()
        if cfg is None:
            return RedirectResponse(
                _callback_redirect(origin, state_data, query="ga4_error=config"),
                status_code=302,
            )

        try:
            import ga4_oauth as g4o

            creds = g4o.exchange_code(cfg.client_id, cfg.client_secret, cfg.redirect_uri, code)
            request.session[SESSION_CREDS_KEY] = g4o.credentials_to_dict(creds)
            request.session.pop("ga4_property_options", None)
        except Exception as exc:
            log.exception("GA4 callback failed: %s", exc)
            request.session["ga4_oauth_error"] = str(exc)
            return RedirectResponse(
                _callback_redirect(origin, state_data, query="ga4_error=exchange"),
                status_code=302,
            )

        return RedirectResponse(
            _callback_redirect(origin, state_data, query="ga4_connected=1"),
            status_code=302,
        )

    @router.put("/selection")
    def ga4_selection(request: Request, body: Ga4PropertyBody) -> dict[str, Any]:
        if not request.session.get(SESSION_CREDS_KEY):
            raise HTTPException(401, "Connect Google Analytics first.")
        pid = body.property_id.strip()
        if not pid.isdigit():
            raise HTTPException(400, "Invalid GA4 property id.")
        request.session[SESSION_PROPERTY_KEY] = pid
        account_id = body.account_id.strip()
        if account_id:
            request.session[SESSION_ACCOUNT_KEY] = account_id
        request.session[SESSION_AI_CHANNELS_KEY] = body.ai_channel_names.strip()
        if body.conversion_event_name is not None:
            request.session[SESSION_CONVERSION_EVENT_KEY] = body.conversion_event_name
        return {"ok": True, "property_id": pid, "account_id": account_id or None}

    @router.delete("/selection")
    def ga4_clear_selection(request: Request) -> dict[str, bool]:
        for key in (
            SESSION_PROPERTY_KEY,
            SESSION_ACCOUNT_KEY,
            SESSION_AI_CHANNELS_KEY,
            SESSION_CONVERSION_EVENT_KEY,
        ):
            request.session.pop(key, None)
        return {"ok": True}

    @router.get("/top-pages")
    def ga4_top_pages(
        request: Request,
        origin: str,
        limit: int = 100,
    ) -> dict[str, Any]:
        creds_dict = request.session.get(SESSION_CREDS_KEY)
        property_id = str(request.session.get(SESSION_PROPERTY_KEY) or "").strip()
        if not isinstance(creds_dict, dict) or not creds_dict.get("refresh_token"):
            raise HTTPException(401, "Connect Google Analytics first.")
        if not property_id:
            raise HTTPException(400, "Select a GA4 property first.")

        raw_origin = origin.strip()
        if not raw_origin.startswith(("http://", "https://")):
            raw_origin = f"https://{raw_origin}"
        parsed = urllib.parse.urlparse(raw_origin)
        target_host = (parsed.hostname or "").lower().removeprefix("www.")
        if not target_host:
            raise HTTPException(400, "A valid website origin is required.")
        result_limit = min(max(limit, 1), 100)

        try:
            import ga4_oauth as g4o

            creds = g4o.credentials_from_dict(creds_dict)
            creds = g4o.ensure_fresh_credentials(creds)
            request.session[SESSION_CREDS_KEY] = g4o.credentials_to_dict(creds)
            rows = []
            for hostname in (target_host, f"www.{target_host}"):
                rows.extend(
                    g4o.fetch_top_pages_last_90_days(
                        creds,
                        property_id,
                        limit=result_limit,
                        hostname=hostname,
                    )
                )
        except Exception as exc:
            log.exception("GA4 top-pages report failed: %s", exc)
            raise HTTPException(502, f"Could not load GA4 top pages: {exc}") from exc

        ranked_rows = sorted(
            (
                row for row in rows
                if str(row.get("host") or "").lower().removeprefix("www.") == target_host
            ),
            key=lambda row: (
                -int(row.get("pageviews") or 0),
                str(row.get("path") or "/"),
            ),
        )
        pages: list[dict[str, Any]] = []
        scheme = parsed.scheme if parsed.scheme in {"http", "https"} else "https"
        seen_urls: set[str] = set()
        for row in ranked_rows:
            host = str(row.get("host") or "").lower()
            path = str(row.get("path") or "/")
            url = f"{scheme}://{host}{path}"
            if url in seen_urls:
                continue
            seen_urls.add(url)
            pages.append({
                "url": url,
                "total_pageviews": int(row.get("pageviews") or 0),
            })
            if len(pages) >= result_limit:
                break

        return {
            "pages": pages,
            "metric": "screenPageViews",
            "date_range": "last_90_days",
            "limit": result_limit,
        }

    @router.post("/disconnect")
    def ga4_disconnect(request: Request) -> dict[str, Any]:
        for key in (
            SESSION_CREDS_KEY,
            SESSION_PROPERTY_KEY,
            SESSION_ACCOUNT_KEY,
            SESSION_AI_CHANNELS_KEY,
            "ga4_property_options",
        ):
            request.session.pop(key, None)
        return {"ok": True}

    return router
