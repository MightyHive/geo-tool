"""GA4 user OAuth for the TypeScript wizard (session-backed)."""

from __future__ import annotations

import base64
import hashlib
import hmac
import json
import logging
import time
from typing import Any

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import RedirectResponse
from pydantic import BaseModel, Field

from api.auth import cookie_secret
from api.auth_config import resolve_web_public_origin
from api.ga4_config import load_ga4_oauth_config

log = logging.getLogger(__name__)

SESSION_CREDS_KEY = "ga4_user_creds_dict"
SESSION_PROPERTY_KEY = "ga4_selected_property_id"
SESSION_ACCOUNT_KEY = "ga4_selected_account_id"
SESSION_AI_CHANNELS_KEY = "ga4_ai_channel_names"


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


def _sign_ga4_state(
    secret: str,
    *,
    wizard_step: int = 2,
    wiz_ga4_after_yes: bool = False,
    ttl_sec: int = 900,
) -> str:
    payload: dict[str, Any] = {
        "e": int(time.time()) + ttl_sec,
        "v": "new_audit",
        "s": int(wizard_step),
    }
    if wiz_ga4_after_yes:
        payload["w"] = 1
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


def _web_origin(request: Request) -> str:
    return resolve_web_public_origin(request)


class Ga4PropertyBody(BaseModel):
    property_id: str = Field(..., min_length=1)
    account_id: str = ""
    ai_channel_names: str = ""


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
        if not code or not state:
            return RedirectResponse(f"{origin}/audit/new?step=2&ga4_error=missing", status_code=302)

        secret = cookie_secret()
        if not _parse_ga4_state(state, secret):
            return RedirectResponse(f"{origin}/audit/new?step=2&ga4_error=state", status_code=302)

        cfg = load_ga4_oauth_config()
        if cfg is None:
            return RedirectResponse(f"{origin}/audit/new?step=2&ga4_error=config", status_code=302)

        try:
            import ga4_oauth as g4o

            creds = g4o.exchange_code(cfg.client_id, cfg.client_secret, cfg.redirect_uri, code)
            request.session[SESSION_CREDS_KEY] = g4o.credentials_to_dict(creds)
            request.session.pop("ga4_property_options", None)
        except Exception as exc:
            log.exception("GA4 callback failed: %s", exc)
            request.session["ga4_oauth_error"] = str(exc)
            return RedirectResponse(f"{origin}/audit/new?step=2&ga4_error=exchange", status_code=302)

        return RedirectResponse(f"{origin}/audit/new?step=2&ga4_connected=1", status_code=302)

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
        return {"ok": True, "property_id": pid, "account_id": account_id or None}

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
