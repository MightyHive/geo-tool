"""Google Cloud Identity-Aware Proxy (IAP) JWT verification."""

from __future__ import annotations

import logging
import os
from dataclasses import dataclass
from functools import lru_cache
from typing import Any

from geo_app_env import current_app_env, load_app_environment

load_app_environment()

log = logging.getLogger(__name__)

IAP_JWT_HEADER = "x-goog-iap-jwt-assertion"
IAP_EMAIL_HEADER = "x-goog-authenticated-user-email"
IAP_USER_ID_HEADER = "x-goog-authenticated-user-id"
IAP_CERTS_URL = "https://www.gstatic.com/iap/verify/public_key"


@dataclass(frozen=True)
class IAPConfig:
    """``audiences``: OAuth client ID (HTTPS LB + IAP) and/or Cloud Run resource path."""

    audiences: tuple[str, ...]
    enforce: bool
    hosted_domain: str | None = None
    dev_user_email: str | None = None


def _truthy(raw: str | None) -> bool:
    return str(raw or "").strip().lower() in {"1", "true", "yes", "on"}


def _read_secrets_iap() -> dict[str, Any]:
    from api.auth_config import _read_secrets_toml

    raw = _read_secrets_toml().get("iap")
    return raw if isinstance(raw, dict) else {}


def _parse_audience_list(raw: str) -> tuple[str, ...]:
    parts = [p.strip() for p in raw.replace("\n", ",").split(",")]
    return tuple(p for p in parts if p)


def _metadata_project_number() -> str | None:
    """Project number for Cloud Run IAP audience (``/projects/NUMBER/...``)."""
    explicit = (os.environ.get("GCP_PROJECT_NUMBER") or "").strip()
    if explicit:
        return explicit
    if not os.environ.get("K_SERVICE"):
        return None
    try:
        import urllib.request

        req = urllib.request.Request(
            "http://metadata.google.internal/computeMetadata/v1/project/numeric-project-id",
            headers={"Metadata-Flavor": "Google"},
        )
        with urllib.request.urlopen(req, timeout=2) as resp:
            return resp.read().decode().strip() or None
    except Exception:
        return None


def _cloud_run_iap_audience() -> str | None:
    """
    When IAP is enabled on a Cloud Run service, JWT ``aud`` is the service resource path,
    not the OAuth client ID shown in the IAP console.
    """
    service = (os.environ.get("K_SERVICE") or "").strip()
    if not service:
        return None
    number = _metadata_project_number()
    if not number:
        return None
    region = (
        (os.environ.get("CLOUD_RUN_REGION") or "").strip()
        or (os.environ.get("GCP_REGION") or "").strip()
        or (os.environ.get("GOOGLE_CLOUD_REGION") or "").strip()
    )
    if not region and os.environ.get("K_REVISION"):
        # e.g. geo-audit-staging-00008-8jr — region is not in revision; rely on env above.
        pass
    if not region:
        return None
    return f"/projects/{number}/locations/{region}/services/{service}"


def _collect_iap_audiences(table: dict[str, Any]) -> tuple[str, ...]:
    raw = (os.environ.get("IAP_AUDIENCE") or table.get("audience") or "").strip()
    oauth_client = (
        os.environ.get("IAP_OAUTH_CLIENT_ID") or table.get("oauth_client_id") or ""
    ).strip()
    seen: set[str] = set()
    out: list[str] = []
    for item in _parse_audience_list(raw) + _parse_audience_list(oauth_client):
        if item not in seen:
            seen.add(item)
            out.append(item)
    cr_aud = _cloud_run_iap_audience()
    if cr_aud and cr_aud not in seen:
        out.append(cr_aud)
    return tuple(out)


def load_iap_config() -> IAPConfig | None:
    """
    IAP is active when at least one audience is configured.

    - **Cloud Run IAP**: JWT audience is
      ``/projects/PROJECT_NUMBER/locations/REGION/services/SERVICE`` (auto-detected on Cloud Run).
    - **HTTPS load balancer + IAP**: audience is usually the OAuth client ID
      (``….apps.googleusercontent.com``). Set ``IAP_OAUTH_CLIENT_ID`` or include it in
      comma-separated ``IAP_AUDIENCE``.

    Set ``IAP_ENABLED=false`` to disable even if audience is configured.
    """
    if str(os.environ.get("IAP_ENABLED") or "").strip().lower() in {
        "0",
        "false",
        "no",
        "off",
    }:
        return None
    table = _read_secrets_iap()
    audiences = _collect_iap_audiences(table)
    if not audiences:
        return None
    explicit_on = _truthy(os.environ.get("IAP_ENABLED")) or _truthy(str(table.get("enabled") or ""))
    if not explicit_on and current_app_env() == "development" and not _truthy(
        str(table.get("enabled") or "")
    ):
        # Local dev: require explicit enable unless secrets [iap] enabled = true
        return None
    enforce = _truthy(os.environ.get("IAP_ENFORCE")) or _truthy(str(table.get("enforce") or ""))
    if not enforce and os.environ.get("IAP_ENFORCE") is None and table.get("enforce") is None:
        # Default: enforce in staging/production, not in development
        enforce = current_app_env() != "development"
    hosted_domain = (
        os.environ.get("IAP_HOSTED_DOMAIN")
        or os.environ.get("GOOGLE_OAUTH_DOMAIN")
        or table.get("hosted_domain")
        or ""
    ).strip() or None
    dev_email = (os.environ.get("IAP_DEV_USER_EMAIL") or table.get("dev_user_email") or "").strip() or None
    return IAPConfig(
        audiences=audiences,
        enforce=enforce,
        hosted_domain=hosted_domain,
        dev_user_email=dev_email,
    )


def iap_enabled() -> bool:
    return load_iap_config() is not None


def _parse_email_header(value: str) -> str:
    """``accounts.google.com:user@example.com`` → ``user@example.com``."""
    v = (value or "").strip()
    if ":" in v:
        return v.split(":", 1)[1].strip().lower()
    return v.lower()


@lru_cache(maxsize=256)
def _verify_iap_jwt_cached(jwt_assertion: str, audience: str) -> dict[str, Any]:
    from google.auth.transport import requests as google_requests
    from google.oauth2 import id_token

    return id_token.verify_token(
        jwt_assertion,
        google_requests.Request(),
        audience=audience,
        certs_url=IAP_CERTS_URL,
    )


def verify_iap_jwt(jwt_assertion: str, cfg: IAPConfig) -> dict[str, str] | None:
    """Validate ``X-Goog-IAP-JWT-Assertion`` and return ``{email, name, sub}``."""
    token = (jwt_assertion or "").strip()
    if not token:
        return None
    claims: dict[str, Any] | None = None
    last_exc: Exception | None = None
    for audience in cfg.audiences:
        try:
            claims = _verify_iap_jwt_cached(token, audience)
            break
        except Exception as exc:
            last_exc = exc
            continue
    if claims is None:
        log.warning(
            "IAP JWT verification failed for audiences %s: %s",
            cfg.audiences,
            last_exc,
        )
        return None

    email = str(claims.get("email") or "").strip().lower()
    if not email:
        return None
    if cfg.hosted_domain:
        hd = str(claims.get("hd") or "")
        if hd and hd != cfg.hosted_domain:
            log.warning("IAP hosted domain mismatch: %r != %r", hd, cfg.hosted_domain)
            return None
    name = str(claims.get("name") or "").strip()
    return {"email": email, "name": name, "sub": str(claims.get("sub") or "")}


def user_from_request_headers(headers: Any, cfg: IAPConfig) -> dict[str, str] | None:
    """Resolve user from IAP JWT (preferred) or verified email header after JWT check."""
    jwt_raw = headers.get(IAP_JWT_HEADER) or headers.get("X-Goog-IAP-JWT-Assertion")
    if jwt_raw:
        user = verify_iap_jwt(str(jwt_raw), cfg)
        if user:
            return user

    # Do not trust email header without a valid JWT in enforce mode
    if cfg.enforce:
        return None

    email_hdr = headers.get(IAP_EMAIL_HEADER) or headers.get("X-Goog-Authenticated-User-Email")
    if email_hdr:
        email = _parse_email_header(str(email_hdr))
        if email:
            return {"email": email, "name": ""}
    return None


def dev_bypass_user(cfg: IAPConfig) -> dict[str, str] | None:
    if cfg.dev_user_email:
        return {"email": cfg.dev_user_email.strip().lower(), "name": "IAP dev bypass"}
    return None
