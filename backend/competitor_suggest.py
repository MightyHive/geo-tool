"""
Suggest competitor site URLs via a short **Google Gemini** request.

**API key (typical for a GCP project key)**  
Set ``GEMINI_API_KEY`` or ``GOOGLE_API_KEY`` in the environment, or in ``.streamlit/secrets.toml`` (read
automatically here and in Streamlit). Enable **Generative Language API** (Gemini) on that project.  
Optional: ``GEMINI_COMPETITOR_MODEL`` (default ``gemini-3.5-flash``).

**Vertex AI (same project, service account / ADC — no browser API key)**  
Set ``GEMINI_USE_VERTEX_AI=1``, ``GOOGLE_CLOUD_PROJECT``, and optionally ``GOOGLE_CLOUD_LOCATION``  
(default ``us-central1``). Uses ``google.auth.default()`` (e.g. ``GOOGLE_APPLICATION_CREDENTIALS``).  
Optional: ``GEMINI_COMPETITOR_MODEL`` for the Vertex model id (default ``gemini-3.5-flash``).
"""

from __future__ import annotations

import json
import os
import re
import ssl
import urllib.error
import urllib.request
from collections.abc import Mapping
from pathlib import Path
from typing import Any
from urllib.parse import quote, urlparse

from geo_market import resolve_primary_market


def _strip_json_fence(raw: str) -> str:
    t = raw.strip()
    if t.startswith("```"):
        t = re.sub(r"^```[a-zA-Z0-9]*\s*", "", t)
        t = re.sub(r"\s*```$", "", t)
    return t.strip()


def _normalize_site_url(u: str) -> str | None:
    """Return canonical origin URL for crawl (e.g. ``https://example.com/``)."""
    s = (u or "").strip()
    if not s:
        return None
    if not re.match(r"^[a-zA-Z][a-zA-Z0-9+.-]*://", s):
        s = "https://" + s
    p = urlparse(s)
    if p.scheme not in ("http", "https") or not p.netloc:
        return None
    return f"{p.scheme}://{p.netloc}/"


def _primary_host(primary_url: str) -> str:
    nu = _normalize_site_url(primary_url)
    if not nu:
        return ""
    return (urlparse(nu).hostname or "").lower()


def _secrets_pick_scalar(container: Any, key: str) -> str:
    """Return a string secret from a mapping/AttrDict; skip nested tables."""
    if container is None or not key:
        return ""
    try:
        if isinstance(container, Mapping):
            raw = container.get(key)
        elif hasattr(container, "__getitem__"):
            raw = container[key]
        else:
            raw = getattr(container, key, None)
        if raw is None:
            return ""
        if isinstance(raw, (Mapping, dict)):
            return ""
        return str(raw).strip()
    except Exception:
        return ""


_SECRETS_TOML_CACHE: dict[str, Any] | None = None


def _secrets_toml_parsed() -> dict[str, Any]:
    """Parse repo ``.streamlit/secrets.toml`` once (CLI / subprocesses have no ``st.secrets``)."""
    global _SECRETS_TOML_CACHE
    if _SECRETS_TOML_CACHE is not None:
        return _SECRETS_TOML_CACHE
    from geo_app_env import REPO_ROOT

    path = REPO_ROOT / ".streamlit" / "secrets.toml"
    if not path.is_file():
        _SECRETS_TOML_CACHE = {}
        return _SECRETS_TOML_CACHE
    try:
        import tomllib

        with path.open("rb") as f:
            raw = tomllib.load(f)
        _SECRETS_TOML_CACHE = raw if isinstance(raw, dict) else {}
    except Exception:
        _SECRETS_TOML_CACHE = {}
    return _SECRETS_TOML_CACHE


def _get_from_toml_tree(data: dict[str, Any], name: str) -> str:
    """Top-level + nested tables (same layout as :func:`_get_config` Streamlit branch)."""
    if not data or not name:
        return ""
    if name in data and not isinstance(data[name], dict):
        return str(data[name]).strip()
    for section in ("llm", "api", "gemini", "keys", "google", "openai", "google_ai", "secrets", "env", "credentials"):
        node = data.get(section)
        if isinstance(node, dict):
            v = _secrets_pick_scalar(node, name)
            if v:
                return v
    auth = data.get("auth")
    if isinstance(auth, dict):
        v = _secrets_pick_scalar(auth, name)
        if v:
            return v
    return ""


def ensure_llm_env_from_streamlit_secrets() -> None:
    """Copy LLM / cloud keys from nested ``st.secrets`` tables into ``os.environ`` when unset.

    Streamlit only promotes **top-level** string/number secrets to the process environment.
    Values under ``[llm]`` and similar are available via ``st.secrets`` but not ``os.environ``,
    so any code that checks ``os.environ`` first (including some library paths) can miss them.
    """
    names = (
        "GEMINI_API_KEY",
        "GOOGLE_API_KEY",
        "GOOGLE_GENAI_API_KEY",
        "GOOGLE_GENERATIVE_AI_API_KEY",
        "GENAI_API_KEY",
        "OPENAI_API_KEY",
        "GOOGLE_CLOUD_PROJECT",
        "GOOGLE_CLOUD_LOCATION",
        "GEMINI_USE_VERTEX_AI",
    )
    try:
        import streamlit as st

        sec = getattr(st, "secrets", None)
    except Exception:
        return
    if sec is None:
        return

    def consider(mapping: Any) -> None:
        if mapping is None:
            return
        for k in names:
            if (os.environ.get(k) or "").strip():
                continue
            v = _secrets_pick_scalar(mapping, k)
            if v:
                os.environ[k] = v

    try:
        consider(sec)
    except Exception:
        pass
    for section in ("llm", "api", "gemini", "google", "keys", "openai", "google_ai", "secrets", "env", "credentials"):
        try:
            if section in sec:
                consider(sec[section])
        except Exception:
            continue


def _get_config(name: str) -> str:
    """Environment first, then ``st.secrets`` when in a Streamlit app, then ``.streamlit/secrets.toml``.

    ``create-report.py`` and ``crawl-site.py`` run as plain subprocesses: ``st.secrets`` is empty, so
    keys only in ``secrets.toml`` are read via **tomllib** from the repo ``.streamlit/`` folder.
    """
    v = (os.environ.get(name) or "").strip()
    if v:
        return v
    try:
        import streamlit as st

        sec = getattr(st, "secrets", None)
        if sec is not None:
            v2 = _secrets_pick_scalar(sec, name)
            if v2:
                return v2

            for section in ("llm", "api", "gemini", "keys", "google", "openai", "google_ai", "secrets", "env", "credentials"):
                try:
                    if section not in sec:
                        continue
                    node = sec[section]
                except Exception:
                    continue
                v3 = _secrets_pick_scalar(node, name)
                if v3:
                    return v3

            try:
                if "auth" in sec:
                    v4 = _secrets_pick_scalar(sec["auth"], name)
                    if v4:
                        return v4
            except Exception:
                pass
    except Exception:
        pass

    v5 = _get_from_toml_tree(_secrets_toml_parsed(), name)
    if v5:
        return v5
    return ""


def _truthy_env(name: str) -> bool:
    raw = (_get_config(name) or "").strip().lower()
    return raw in ("1", "true", "yes", "on")


def _gemini_api_key() -> str:
    ensure_llm_env_from_streamlit_secrets()
    for k in (
        "GEMINI_API_KEY",
        "GOOGLE_API_KEY",
        "GOOGLE_GENAI_API_KEY",
        "GOOGLE_GENERATIVE_AI_API_KEY",
        "GENAI_API_KEY",
    ):
        v = _get_config(k)
        if v:
            return v
    try:
        import streamlit as st

        sec = getattr(st, "secrets", None)
        if sec is None:
            return ""
        for section in ("llm", "gemini", "google", "api", "keys", "secrets", "env", "credentials"):
            if section not in sec:
                continue
            try:
                node = sec[section]
            except Exception:
                continue
            for leaf in ("api_key", "API_KEY", "key", "gemini_api_key"):
                s = _secrets_pick_scalar(node, leaf)
                if s:
                    return s
    except Exception:
        pass
    return ""


def _default_model_vertex() -> str:
    return (_get_config("GEMINI_COMPETITOR_MODEL") or "gemini-3.5-flash").strip()


def _default_model_google_ai() -> str:
    return (_get_config("GEMINI_COMPETITOR_MODEL") or "gemini-3.5-flash").strip()


def _https_ssl_context() -> ssl.SSLContext:
    """CA bundle for ``urllib`` (fixes macOS ``CERTIFICATE_VERIFY_FAILED`` when system certs are missing)."""
    try:
        import certifi

        return ssl.create_default_context(cafile=certifi.where())
    except Exception:
        return ssl.create_default_context()


def _post_json(url: str, body: dict, headers: dict[str, str], timeout: int = 60) -> dict:
    data = json.dumps(body).encode("utf-8")
    hdrs = {"Content-Type": "application/json", **headers}
    req = urllib.request.Request(url, data=data, headers=hdrs, method="POST")
    ctx = _https_ssl_context() if url.lower().startswith("https:") else None
    try:
        with urllib.request.urlopen(req, timeout=timeout, context=ctx) as resp:
            return json.loads(resp.read().decode("utf-8"))
    except urllib.error.HTTPError as e:
        detail = e.read().decode("utf-8", errors="replace")[:1200]
        raise ValueError(f"Gemini HTTP error ({e.code}): {detail}") from e
    except urllib.error.URLError as e:
        raise ValueError(f"Could not reach Gemini API: {e}") from e


def _response_text(payload: dict) -> str:
    cands = payload.get("candidates")
    if not isinstance(cands, list) or not cands:
        err = payload.get("error")
        if isinstance(err, dict):
            raise ValueError(f"Gemini API error: {err.get('message', err)!r}")
        raise ValueError(f"Gemini returned no candidates: {payload!r}")
    content = cands[0].get("content") or {}
    parts = content.get("parts") or []
    if not isinstance(parts, list):
        raise ValueError(f"Unexpected Gemini content shape: {payload!r}")
    return "".join(str(p.get("text") or "") for p in parts if isinstance(p, dict))


def _generate_via_api_key(
    *,
    api_key: str,
    model: str,
    system_instruction: str,
    user_text: str,
) -> str:
    q = quote(api_key, safe="")
    url = f"https://generativelanguage.googleapis.com/v1beta/models/{quote(model, safe='')}:generateContent?key={q}"
    body: dict = {
        "systemInstruction": {"parts": [{"text": system_instruction}]},
        "contents": [{"role": "user", "parts": [{"text": user_text}]}],
        "generationConfig": {
            "temperature": 0.3,
            "maxOutputTokens": 1024,
        },
    }
    payload = _post_json(url, body, {})
    return _response_text(payload)


def _generate_via_vertex(
    *,
    project: str,
    location: str,
    model: str,
    system_instruction: str,
    user_text: str,
) -> str:
    from google.auth import default as google_auth_default
    from google.auth.transport.requests import Request as GoogleAuthRequest

    creds, _ = google_auth_default(scopes=["https://www.googleapis.com/auth/cloud-platform"])
    creds.refresh(GoogleAuthRequest())
    token = getattr(creds, "token", None) or ""
    if not token:
        raise ValueError("Could not obtain an access token from Application Default Credentials.")

    loc = quote(location, safe="")
    proj = quote(project, safe="")
    mod = quote(model, safe="")
    url = (
        f"https://{loc}-aiplatform.googleapis.com/v1/projects/{proj}/locations/{loc}/"
        f"publishers/google/models/{mod}:generateContent"
    )
    body: dict = {
        "systemInstruction": {"parts": [{"text": system_instruction}]},
        "contents": [{"role": "user", "parts": [{"text": user_text}]}],
        "generationConfig": {
            "temperature": 0.3,
            "maxOutputTokens": 1024,
        },
    }
    payload = _post_json(url, body, {"Authorization": f"Bearer {token}"})
    return _response_text(payload)


def suggest_competitor_urls(
    brand: str,
    *,
    primary_url: str = "",
    industry: str = "",
    max_suggestions: int = 5,
    market_country: str = "",
    market_country_code: str = "",
) -> list[str]:
    """
    Return up to ``max_suggestions`` https URLs, excluding the primary site's host.
    Raises ``ValueError`` on missing configuration, bad brand, or unusable API response.
    """
    brand = (brand or "").strip()
    if not brand:
        raise ValueError("Brand name is required to search for competitors.")

    api_key = _gemini_api_key()
    use_vertex = _truthy_env("GEMINI_USE_VERTEX_AI")
    project = (_get_config("GOOGLE_CLOUD_PROJECT") or "").strip()
    location = (_get_config("GOOGLE_CLOUD_LOCATION") or "us-central1").strip()

    if api_key:
        model = _default_model_google_ai()
        transport = "Google AI (API key)"
    elif use_vertex and project:
        model = _default_model_vertex()
        transport = "Vertex AI (ADC)"
    else:
        raise ValueError(
            "Configure Gemini for competitor search: set **GEMINI_API_KEY** or **GOOGLE_API_KEY** "
            "(``export …`` for CLI runs, or top-level / ``[llm]`` in ``.streamlit/secrets.toml`` — "
            "``create-report.py`` reads that file even outside Streamlit), or set **GEMINI_USE_VERTEX_AI=1** "
            "with **GOOGLE_CLOUD_PROJECT** and Application Default Credentials (e.g. **GOOGLE_APPLICATION_CREDENTIALS**). "
            "**OPENAI_API_KEY** is only used for Prompt performance live probes, not competitor search."
        )

    phost = _primary_host(primary_url)
    ind = (industry or "").strip()
    mc, mid = resolve_primary_market(market_country, market_country_code)

    system_instruction = (
        "You help with marketing competitive analysis. Reply with a single JSON object only, no markdown fences. "
        'Schema: {"urls": ["https://example.com/", ...]}. '
        "List the brand's main direct competitors as public marketing websites (https), "
        "real companies only, no placeholders. Prefer official homepages. "
        f"At most {max_suggestions} URLs."
    )
    if mc or mid:
        system_instruction += (
            f" Prioritize competitors that meaningfully serve or operate in **{mc}"
            + (f" ({mid})" if mid else "")
            + "** when that affects who counts as a peer."
        )
    user_payload: dict[str, Any] = {
        "brand": brand,
        "primary_site": primary_url or None,
        "industry": ind or None,
        "primary_market_country": mc or None,
        "primary_market_country_code": mid or None,
    }
    user_text = json.dumps(user_payload, ensure_ascii=False)

    try:
        if api_key:
            content = _generate_via_api_key(
                api_key=api_key,
                model=model,
                system_instruction=system_instruction,
                user_text=user_text,
            )
        else:
            content = _generate_via_vertex(
                project=project,
                location=location,
                model=model,
                system_instruction=system_instruction,
                user_text=user_text,
            )
    except ValueError:
        raise
    except Exception as e:
        raise ValueError(f"Gemini request failed ({transport}): {e}") from e

    try:
        obj = json.loads(_strip_json_fence(str(content)))
    except json.JSONDecodeError as e:
        raise ValueError(f"Model did not return valid JSON: {content[:500]!r}") from e

    raw_urls = obj.get("urls")
    if not isinstance(raw_urls, list):
        raise ValueError('Expected JSON object with a "urls" array.')

    out: list[str] = []
    seen: set[str] = set()
    for item in raw_urls:
        if isinstance(item, dict) and "url" in item:
            item = item.get("url")
        if not isinstance(item, str):
            continue
        nu = _normalize_site_url(item)
        if not nu:
            continue
        host = (urlparse(nu).hostname or "").lower()
        if phost and host == phost:
            continue
        if host in seen:
            continue
        seen.add(host)
        out.append(nu)
        if len(out) >= max_suggestions:
            break

    if not out:
        raise ValueError("The model returned no usable competitor URLs. Try again or enter URLs manually.")

    return out
