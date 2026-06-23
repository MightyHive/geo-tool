"""Attach IAP identity to each request; optionally reject unauthenticated traffic."""

from __future__ import annotations

import logging
from typing import Callable

from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request
from starlette.responses import JSONResponse, Response

from api.iap import dev_bypass_user, iap_enabled, load_iap_config, user_from_request_headers

log = logging.getLogger(__name__)

# Paths that must work without IAP (health checks, OpenAPI in dev)
_IAP_EXEMPT_PREFIXES = (
    "/api/health",
    "/openapi.json",
    "/docs",
    "/redoc",
)


class IAPMiddleware(BaseHTTPMiddleware):
    """Populate ``request.state.iap_user`` from verified IAP JWT headers."""

    async def dispatch(self, request: Request, call_next: Callable) -> Response:
        cfg = load_iap_config()
        if cfg is None:
            return await call_next(request)

        user = user_from_request_headers(request.headers, cfg)
        if user is None and not cfg.enforce:
            user = dev_bypass_user(cfg)

        if user:
            request.state.iap_user = user
        elif cfg.enforce and not _is_exempt(request.url.path):
            log.info("IAP enforce: rejected %s (no valid JWT)", request.url.path)
            return JSONResponse(
                status_code=401,
                content={
                    "detail": "Identity-Aware Proxy authentication required",
                    "auth_mode": "iap",
                },
            )

        return await call_next(request)


def _is_exempt(path: str) -> bool:
    return any(path == p or path.startswith(p + "/") for p in _IAP_EXEMPT_PREFIXES)


def get_iap_user(request: Request) -> dict[str, str] | None:
    user = getattr(request.state, "iap_user", None)
    if isinstance(user, dict) and user.get("email"):
        return {
            "email": str(user["email"]).strip().lower(),
            "name": str(user.get("name") or "").strip(),
        }
    return None
