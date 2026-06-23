"""
Application environment (development / staging / production).

- Set ``APP_ENV`` or ``GEO_ENV`` to ``development``, ``staging``, or ``production``
  (aliases: ``dev``, ``stage``, ``prod``). Defaults to ``development``.
- Optional dotenv files (never override variables already set in the process env):

  1. Repo root ``.env`` — optional; use to set ``APP_ENV`` when not exporting it.
  2. ``env/.env.<environment>`` — e.g. ``env/.env.staging`` for host-specific values.

Copy the ``env/.env.*.example`` files to the real names and fill values. Do not commit secrets.
"""

from __future__ import annotations

import os
from pathlib import Path

BACKEND_ROOT = Path(__file__).resolve().parent
REPO_ROOT = BACKEND_ROOT.parent
ASSETS_ROOT = REPO_ROOT / "assets"

_VALID = frozenset({"development", "staging", "production"})
_ALIASES = {
    "dev": "development",
    "development": "development",
    "stage": "staging",
    "staging": "staging",
    "prod": "production",
    "production": "production",
}


def normalize_app_env(raw: str | None) -> str:
    if not raw or not str(raw).strip():
        return "development"
    key = str(raw).strip().lower()
    return _ALIASES.get(key, "development" if key not in _VALID else key)


def current_app_env() -> str:
    """Resolved environment name: ``development`` | ``staging`` | ``production``."""
    return normalize_app_env(os.environ.get("APP_ENV") or os.environ.get("GEO_ENV"))


def app_env_display_label() -> str:
    return {
        "development": "Development",
        "staging": "Staging",
        "production": "Production",
    }.get(current_app_env(), current_app_env())


def _load_dotenv_file(path: Path) -> None:
    if not path.is_file():
        return
    try:
        text = path.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return
    for raw in text.splitlines():
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        if line.startswith("export "):
            line = line[7:].strip()
        if "=" not in line:
            continue
        key, _, val = line.partition("=")
        key = key.strip()
        if not key or key in os.environ:
            continue
        val = val.strip()
        if len(val) >= 2 and val[0] == val[-1] and val[0] in {'"', "'"}:
            val = val[1:-1]
        os.environ[key] = val


def load_app_environment() -> str:
    """
    Load layered env files and return the active environment name.

    Load order (only sets keys that are **not** already in ``os.environ``):

    1. Repo root ``.env`` — set ``APP_ENV`` here for local runs if you do not export it.
    2. ``env/.env.<APP_ENV>`` — e.g. ``env/.env.staging`` for host-specific values.
    """
    _load_dotenv_file(REPO_ROOT / ".env")
    env_name = current_app_env()
    _load_dotenv_file(REPO_ROOT / "env" / f".env.{env_name}")
    return env_name


_LEGACY_DEPLOY_ORIGIN = "https://automated-posted-carmaker.ngrok-free.dev"


def default_deploy_public_origin() -> str:
    """
    Public origin for OAuth redirects when ``WEB_PUBLIC_ORIGIN`` is unset (no trailing slash).

    Precedence: ``DEPLOY_PUBLIC_ORIGIN`` → ``STREAMLIT_PUBLIC_ORIGIN`` (deprecated alias)
    → legacy shared default (replace via ``env/.env.staging`` / ``env/.env.production`` for real deploys).
    """
    for key in ("DEPLOY_PUBLIC_ORIGIN", "STREAMLIT_PUBLIC_ORIGIN"):
        explicit = (os.environ.get(key) or "").strip().rstrip("/")
        if explicit:
            return explicit
    return _LEGACY_DEPLOY_ORIGIN.rstrip("/")
