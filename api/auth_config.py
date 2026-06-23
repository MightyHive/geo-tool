"""Load Google OAuth settings from ``.streamlit/secrets.toml`` [auth] or env vars."""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from geo_app_env import (
    REPO_ROOT,
    current_app_env,
    default_deploy_public_origin,
    load_app_environment,
)

load_app_environment()

_SECRETS_PATH = REPO_ROOT / ".streamlit" / "secrets.toml"


@dataclass(frozen=True)
class AuthConfig:
    client_id: str
    client_secret: str
    cookie_secret: str
    server_metadata_url: str
    redirect_uri: str
    web_public_origin: str
    hosted_domain: str | None = None


def default_web_public_origin() -> str:
    """Origin of the React app (no trailing slash)."""
    explicit = (os.environ.get("WEB_PUBLIC_ORIGIN") or "").strip().rstrip("/")
    if explicit:
        return explicit
    if current_app_env() == "development":
        return "http://localhost:5173"
    return default_deploy_public_origin()


def allowed_web_origins() -> frozenset[str]:
    """Browser origins allowed for OAuth redirect (must match Google Cloud Console)."""
    origins: set[str] = {
        "http://localhost:5173",
        "http://127.0.0.1:5173",
        default_web_public_origin(),
    }
    for key in ("WEB_PUBLIC_ORIGIN", "DEPLOY_PUBLIC_ORIGIN", "STREAMLIT_PUBLIC_ORIGIN"):
        v = (os.environ.get(key) or "").strip().rstrip("/")
        if v:
            origins.add(v)
    return frozenset(o for o in origins if o)


def oauth_callback_uri(web_origin: str) -> str:
    explicit = (os.environ.get("AUTH_REDIRECT_URI") or "").strip()
    if explicit:
        return explicit
    return f"{web_origin.rstrip('/')}/api/auth/callback"


def resolve_web_public_origin(request) -> str:
    """
    Pick the web app origin from the incoming request (via Vite ``X-Forwarded-*`` headers).

    Login and callback must use the same host the user opened in the browser
    (``localhost`` vs ``127.0.0.1`` are different cookie domains).
    """
    allowed = allowed_web_origins()

    fwd_host = (request.headers.get("x-forwarded-host") or "").split(",")[0].strip()
    fwd_proto = (request.headers.get("x-forwarded-proto") or "http").split(",")[0].strip()
    if fwd_host:
        candidate = f"{fwd_proto}://{fwd_host}".rstrip("/")
        if candidate in allowed:
            return candidate

    referer = (request.headers.get("referer") or "").strip()
    if referer:
        from urllib.parse import urlparse

        u = urlparse(referer)
        if u.scheme and u.netloc:
            candidate = f"{u.scheme}://{u.netloc}"
            if candidate in allowed:
                return candidate

    return default_web_public_origin()


def _read_secrets_toml() -> dict[str, Any]:
    if not _SECRETS_PATH.is_file():
        return {}
    try:
        import tomllib
    except ImportError:  # pragma: no cover
        import tomli as tomllib  # type: ignore[no-redef]
    try:
        return tomllib.loads(_SECRETS_PATH.read_text(encoding="utf-8"))
    except Exception:
        return {}


def _auth_table(raw: dict[str, Any]) -> dict[str, Any]:
    auth = raw.get("auth")
    if isinstance(auth, dict):
        return auth
    return {}


def load_auth_config() -> AuthConfig | None:
    """
  Return OAuth settings when sign-in is configured.

  Uses ``[auth]`` from ``.streamlit/secrets.toml`` plus optional env overrides.
  Web redirect URI is always
  ``{WEB_PUBLIC_ORIGIN}/api/auth/callback`` — add that URI in Google Cloud Console.
  """
    table = _auth_table(_read_secrets_toml())
    client_id = (
        os.environ.get("GOOGLE_CLIENT_ID")
        or os.environ.get("AUTH_CLIENT_ID")
        or table.get("client_id")
        or ""
    ).strip()
    client_secret = (
        os.environ.get("GOOGLE_CLIENT_SECRET")
        or os.environ.get("AUTH_CLIENT_SECRET")
        or table.get("client_secret")
        or ""
    ).strip()
    cookie_secret = (
        os.environ.get("AUTH_COOKIE_SECRET")
        or table.get("cookie_secret")
        or ""
    ).strip()
    server_metadata_url = (
        os.environ.get("AUTH_SERVER_METADATA_URL")
        or table.get("server_metadata_url")
        or "https://accounts.google.com/.well-known/openid-configuration"
    ).strip()
    web_origin = default_web_public_origin()
    redirect_uri = (
        os.environ.get("AUTH_REDIRECT_URI")
        or f"{web_origin}/api/auth/callback"
    ).strip()
    hosted_domain = (os.environ.get("GOOGLE_OAUTH_DOMAIN") or "").strip() or None

    if not all([client_id, client_secret, cookie_secret]):
        return None
    return AuthConfig(
        client_id=client_id,
        client_secret=client_secret,
        cookie_secret=cookie_secret,
        server_metadata_url=server_metadata_url,
        redirect_uri=redirect_uri,
        web_public_origin=web_origin,
        hosted_domain=hosted_domain,
    )


def auth_enabled() -> bool:
    return load_auth_config() is not None
