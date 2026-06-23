"""Authentication: Google Cloud IAP (production) or Google OAuth (local dev)."""

from __future__ import annotations

import logging
import os
from typing import Any, Literal

from authlib.integrations.starlette_client import OAuth
from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import RedirectResponse
from starlette.responses import Response

from api.auth_config import (
    AuthConfig,
    load_auth_config,
    oauth_callback_uri,
    resolve_web_public_origin,
)
from api.iap import iap_enabled, load_iap_config
from api.iap_middleware import get_iap_user

log = logging.getLogger(__name__)

SESSION_USER_KEY = "user"
_oauth: OAuth | None = None

AuthMode = Literal["iap", "oauth", "none"]


def auth_mode() -> AuthMode:
    if iap_enabled():
        return "iap"
    if load_auth_config() is not None:
        return "oauth"
    return "none"


def get_oauth() -> OAuth | None:
    global _oauth
    if iap_enabled():
        return None
    cfg = load_auth_config()
    if cfg is None:
        return None
    if _oauth is None:
        oauth = OAuth()
        client_kwargs: dict[str, Any] = {
            "scope": "openid email profile",
        }
        if cfg.hosted_domain:
            client_kwargs["hd"] = cfg.hosted_domain
        oauth.register(
            name="google",
            client_id=cfg.client_id,
            client_secret=cfg.client_secret,
            server_metadata_url=cfg.server_metadata_url,
            client_kwargs=client_kwargs,
        )
        _oauth = oauth
    return _oauth


def cookie_secret() -> str:
    cfg = load_auth_config()
    if cfg is not None:
        return cfg.cookie_secret
    if load_iap_config() is not None:
        return (os.environ.get("AUTH_COOKIE_SECRET") or "").strip() or "geo-iap-session-secret"
    return "geo-dev-insecure-change-me"


def current_user(request: Request) -> dict[str, str] | None:
    """IAP identity takes precedence over OAuth session cookie."""
    iap_user = get_iap_user(request)
    if iap_user:
        return iap_user

    raw = request.session.get(SESSION_USER_KEY)
    if not isinstance(raw, dict):
        return None
    email = str(raw.get("email") or "").strip().lower()
    if not email:
        return None
    return {
        "email": email,
        "name": str(raw.get("name") or "").strip(),
    }


def require_user(request: Request) -> dict[str, str]:
    user = current_user(request)
    if user is None:
        detail = (
            "IAP authentication required"
            if auth_mode() == "iap"
            else "Sign in required"
        )
        raise HTTPException(401, detail=detail)
    return user


def auth_enabled() -> bool:
    return auth_mode() != "none"


def _safe_return_path(raw: str | None, *, cfg: AuthConfig) -> str:
    if not raw:
        return "/"
    path = raw.strip()
    if not path.startswith("/") or path.startswith("//"):
        return "/"
    if "://" in path:
        return "/"
    return path


def auth_status_payload(request: Request) -> dict[str, Any]:
    mode = auth_mode()
    user = current_user(request)
    cfg = load_auth_config()
    iap_cfg = load_iap_config()
    return {
        "mode": mode,
        "enabled": mode != "none",
        "logged_in": user is not None,
        "user": user,
        "login_url": "/api/auth/login" if mode == "oauth" else None,
        "logout_available": mode == "oauth",
        "iap_enforce": bool(iap_cfg.enforce) if iap_cfg else False,
        "redirect_uri": oauth_callback_uri(resolve_web_public_origin(request))
        if cfg
        else None,
        "web_public_origin": resolve_web_public_origin(request) if cfg else None,
    }


def create_auth_router() -> APIRouter:
    router = APIRouter(prefix="/api/auth", tags=["auth"])

    @router.get("/status")
    def auth_status(request: Request) -> dict[str, Any]:
        return auth_status_payload(request)

    @router.get("/login")
    async def login(request: Request, return_to: str = "/") -> Response:
        if auth_mode() == "iap":
            raise HTTPException(
                400,
                "App login is disabled: identity is provided by Google Cloud IAP at the load balancer.",
            )
        cfg = load_auth_config()
        oauth = get_oauth()
        if cfg is None or oauth is None:
            raise HTTPException(
                503,
                "Google sign-in is not configured. Add [auth] to .streamlit/secrets.toml.",
            )
        origin = resolve_web_public_origin(request)
        redirect_uri = oauth_callback_uri(origin)
        safe = _safe_return_path(return_to, cfg=cfg)
        request.session["oauth_return_to"] = safe
        request.session["oauth_web_origin"] = origin
        return await oauth.google.authorize_redirect(request, redirect_uri)

    @router.get("/callback")
    async def callback(request: Request) -> Response:
        if auth_mode() == "iap":
            raise HTTPException(400, "OAuth callback is not used when IAP is enabled.")
        cfg = load_auth_config()
        oauth = get_oauth()
        if cfg is None or oauth is None:
            raise HTTPException(503, "OAuth not configured")
        origin = str(request.session.pop("oauth_web_origin", "") or "").strip()
        if not origin:
            origin = resolve_web_public_origin(request)

        try:
            token = await oauth.google.authorize_access_token(request)
        except Exception as exc:
            log.exception("OAuth callback failed: %s", exc)
            err = "state" if "mismatching_state" in str(exc).lower() else "1"
            dest = f"{origin}/?auth_error={err}"
            return RedirectResponse(dest, status_code=302)

        userinfo = token.get("userinfo") or {}
        if cfg.hosted_domain:
            hd = str(userinfo.get("hd") or "")
            if hd != cfg.hosted_domain:
                dest = f"{origin}/?auth_error=domain"
                return RedirectResponse(dest, status_code=302)

        email = str(userinfo.get("email") or "").strip().lower()
        if not email:
            dest = f"{origin}/?auth_error=email"
            return RedirectResponse(dest, status_code=302)

        request.session[SESSION_USER_KEY] = {
            "email": email,
            "name": str(userinfo.get("name") or "").strip(),
        }
        return_to = _safe_return_path(
            request.session.pop("oauth_return_to", None),
            cfg=cfg,
        )
        log.info("User signed in via OAuth: %s", email)
        return RedirectResponse(f"{origin}{return_to}", status_code=302)

    @router.post("/logout")
    def logout(request: Request) -> dict[str, Any]:
        if auth_mode() == "iap":
            return {
                "ok": False,
                "detail": "Sign-out is handled by Google Cloud IAP (close browser or revoke IAP access).",
            }
        request.session.clear()
        return {"ok": True}

    @router.get("/logout")
    def logout_get(request: Request, return_to: str = "/") -> RedirectResponse:
        from api.auth_config import default_web_public_origin

        cfg = load_auth_config()
        origin = cfg.web_public_origin if cfg else default_web_public_origin()
        path = _safe_return_path(return_to, cfg=cfg) if cfg else "/"
        if auth_mode() == "oauth":
            request.session.clear()
        return RedirectResponse(f"{origin}{path}", status_code=302)

    return router
