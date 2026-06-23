"""GA4 OAuth client settings for the FastAPI web UI."""

from __future__ import annotations

import os
from dataclasses import dataclass
from typing import Any

from api.auth_config import _auth_table, _read_secrets_toml, default_web_public_origin
from geo_app_env import load_app_environment

load_app_environment()


@dataclass(frozen=True)
class Ga4OAuthConfig:
    client_id: str
    client_secret: str
    redirect_uri: str


def _ga4_table(raw: dict[str, Any]) -> dict[str, Any]:
    section = raw.get("ga4_oauth")
    if isinstance(section, dict):
        return section
    return {}


def ga4_redirect_uri_default() -> str:
    explicit = (os.environ.get("GA4_OAUTH_REDIRECT_URI") or "").strip()
    if explicit:
        return explicit
    table = _ga4_table(_read_secrets_toml())
    from_secrets = (table.get("redirect_uri") or "").strip()
    if from_secrets:
        return from_secrets
    return f"{default_web_public_origin()}/api/ga4/callback"


def _oauth_client_from_env_or_tables(
    table: dict[str, Any], auth: dict[str, Any]
) -> tuple[str, str]:
    """Resolve OAuth web client (GA4 block, then [auth], then standard env names)."""
    cid = (
        os.environ.get("GA4_OAUTH_CLIENT_ID")
        or table.get("client_id")
        or auth.get("client_id")
        or os.environ.get("GOOGLE_CLIENT_ID")
        or os.environ.get("AUTH_CLIENT_ID")
        or ""
    ).strip()
    csec = (
        os.environ.get("GA4_OAUTH_CLIENT_SECRET")
        or table.get("client_secret")
        or auth.get("client_secret")
        or os.environ.get("GOOGLE_CLIENT_SECRET")
        or os.environ.get("AUTH_CLIENT_SECRET")
        or ""
    ).strip()
    return cid, csec


def load_ga4_oauth_config() -> Ga4OAuthConfig | None:
    raw = _read_secrets_toml()
    table = _ga4_table(raw)
    auth = _auth_table(raw)
    cid, csec = _oauth_client_from_env_or_tables(table, auth)
    ruri = ga4_redirect_uri_default()
    if cid and csec:
        return Ga4OAuthConfig(client_id=cid, client_secret=csec, redirect_uri=ruri)
    return None


def ga4_redirect_uri_for_request(web_origin: str) -> str:
    """Callback URL shown in the wizard (uses request origin when env is unset)."""
    explicit = (os.environ.get("GA4_OAUTH_REDIRECT_URI") or "").strip()
    if explicit:
        return explicit
    origin = web_origin.strip().rstrip("/")
    if origin:
        return f"{origin}/api/ga4/callback"
    return ga4_redirect_uri_default()
