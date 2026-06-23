"""
Google OAuth for GA4 (Data API + Admin API read-only).

Used by the deployed FastAPI + TypeScript web UI so users can
connect a Google account, pick a property they can access, and run reports with user
credentials (instead of a service account JSON).

CLI scripts (e.g. ``research/ga4_channel_export.py``) use the same **web OAuth**
flow as the deployed app: ``build_flow`` → browser consent → ``exchange_code`` with
``{WEB_PUBLIC_ORIGIN}/api/ga4/callback`` (or ``GA4_OAUTH_REDIRECT_URI`` on Cloud Run).

Requires ``google-auth-oauthlib``. Enable **Google Analytics Admin API** and
**Google Analytics Data API** for your OAuth client’s GCP project.
"""

from __future__ import annotations

import json
import os
import secrets
import tempfile
import time
import tomllib
import urllib.parse
import webbrowser
from dataclasses import dataclass
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path
from threading import Event
from typing import Any

from google.auth.transport.requests import AuthorizedSession, Request
from google.oauth2.credentials import Credentials

from geo_app_env import REPO_ROOT

_REPO_ROOT = REPO_ROOT
_DEFAULT_SECRETS_PATH = _REPO_ROOT / ".streamlit" / "secrets.toml"
_DEFAULT_CLI_TOKEN_PATH = _REPO_ROOT / "research" / ".ga4_oauth_token.json"
_LOCAL_CALLBACK_HOSTS = frozenset({"localhost", "127.0.0.1"})

# Data + Admin list (accountSummaries) — read-only, plus OpenID scopes when the same
# Web OAuth client is used for sign-in and GA4. Google returns a combined scope set;
# oauthlib errors if the requested set does not match (RFC 6749 §3.3).
GA4_OAUTH_SCOPES: tuple[str, ...] = (
    "openid",
    "https://www.googleapis.com/auth/userinfo.email",
    "https://www.googleapis.com/auth/userinfo.profile",
    "https://www.googleapis.com/auth/analytics.readonly",
)


@dataclass(frozen=True)
class OAuthClientConfig:
    client_id: str
    client_secret: str
    redirect_uri: str


def _read_repo_secrets(path: Path | None = None) -> dict[str, Any]:
    secrets_path = path or _DEFAULT_SECRETS_PATH
    if not secrets_path.is_file():
        return {}
    try:
        return tomllib.loads(secrets_path.read_text(encoding="utf-8"))
    except (OSError, tomllib.TOMLDecodeError):
        return {}


def ga4_redirect_uri_default(secrets_path: Path | None = None) -> str:
    """
    GA4 OAuth callback URL — mirrors ``api.ga4_config.ga4_redirect_uri_default``.

    Deployed Cloud Run uses ``{SERVICE_URL}/api/ga4/callback`` via ``GA4_OAUTH_REDIRECT_URI``.
    Local dev defaults to ``http://localhost:5173/api/ga4/callback`` (``WEB_PUBLIC_ORIGIN``).
    """
    explicit = (os.environ.get("GA4_OAUTH_REDIRECT_URI") or "").strip()
    if explicit:
        return explicit
    raw = _read_repo_secrets(secrets_path)
    ga4_table = raw.get("ga4_oauth") if isinstance(raw.get("ga4_oauth"), dict) else {}
    from_secrets = (ga4_table.get("redirect_uri") or "").strip()
    if from_secrets:
        return from_secrets
    origin = (os.environ.get("WEB_PUBLIC_ORIGIN") or "http://localhost:5173").strip().rstrip("/")
    return f"{origin}/api/ga4/callback"


def _oauth_client_from_env_or_tables(
    ga4_table: dict[str, Any],
    auth_table: dict[str, Any],
) -> tuple[str, str]:
    """Mirrors ``api.ga4_config._oauth_client_from_env_or_tables``."""
    cid = (
        os.environ.get("GA4_OAUTH_CLIENT_ID")
        or ga4_table.get("client_id")
        or auth_table.get("client_id")
        or os.environ.get("GOOGLE_CLIENT_ID")
        or os.environ.get("AUTH_CLIENT_ID")
        or ""
    )
    csec = (
        os.environ.get("GA4_OAUTH_CLIENT_SECRET")
        or ga4_table.get("client_secret")
        or auth_table.get("client_secret")
        or os.environ.get("GOOGLE_CLIENT_SECRET")
        or os.environ.get("AUTH_CLIENT_SECRET")
        or ""
    )
    return str(cid).strip(), str(csec).strip()


def load_oauth_config(secrets_path: Path | None = None) -> OAuthClientConfig | None:
    """
    Resolve OAuth web client settings — same as the deployed FastAPI app.

    Delegates to ``api.ga4_config.load_ga4_oauth_config`` when available; otherwise
    mirrors that module's env + secrets resolution inline.
    """
    try:
        from geo_app_env import load_app_environment

        load_app_environment()
        from api.ga4_config import load_ga4_oauth_config

        cfg = load_ga4_oauth_config()
        if cfg is not None:
            return OAuthClientConfig(
                client_id=cfg.client_id,
                client_secret=cfg.client_secret,
                redirect_uri=cfg.redirect_uri,
            )
    except ImportError:
        pass

    raw = _read_repo_secrets(secrets_path)
    ga4_table = raw.get("ga4_oauth") if isinstance(raw.get("ga4_oauth"), dict) else {}
    auth_table = raw.get("auth") if isinstance(raw.get("auth"), dict) else {}
    cid, csec = _oauth_client_from_env_or_tables(ga4_table, auth_table)
    if not (cid and csec):
        return None
    return OAuthClientConfig(
        client_id=cid,
        client_secret=csec,
        redirect_uri=ga4_redirect_uri_default(secrets_path),
    )


def resolve_oauth_client_credentials(
    secrets_path: Path | None = None,
) -> tuple[str, str]:
    """Return ``(client_id, client_secret)`` from :func:`load_oauth_config`."""
    cfg = load_oauth_config(secrets_path)
    if cfg is None:
        return "", ""
    return cfg.client_id, cfg.client_secret


def credentials_to_adc_dict(creds: Credentials) -> dict[str, Any]:
    """``authorized_user`` JSON compatible with ``google.auth.default()``."""
    creds = ensure_fresh_credentials(creds)
    if not creds.client_id or not creds.client_secret or not creds.refresh_token:
        raise ValueError("Credentials missing client_id, client_secret, or refresh_token")
    payload: dict[str, Any] = {
        "type": "authorized_user",
        "client_id": creds.client_id,
        "client_secret": creds.client_secret,
        "refresh_token": creds.refresh_token,
    }
    if creds.token:
        payload["token"] = creds.token
    return payload


def load_adc_credentials(path: Path) -> Credentials | None:
    """Load user credentials saved by :func:`save_adc_credentials`."""
    if not path.is_file():
        return None
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    if data.get("type") == "authorized_user":
        return Credentials(
            token=data.get("token"),
            refresh_token=data.get("refresh_token"),
            token_uri="https://oauth2.googleapis.com/token",
            client_id=str(data["client_id"]),
            client_secret=str(data["client_secret"]),
            scopes=tuple(data.get("scopes") or GA4_OAUTH_SCOPES),
        )
    return credentials_from_dict(data)


def save_adc_credentials(creds: Credentials, path: Path) -> Path:
    path = path.expanduser().resolve()
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(credentials_to_adc_dict(creds), indent=2) + "\n", encoding="utf-8")
    os.chmod(path, 0o600)
    return path


def _extract_oauth_code(raw: str) -> str:
    s = raw.strip()
    if not s:
        raise ValueError("Empty authorization code")
    if "code=" in s:
        parsed = urllib.parse.urlparse(s)
        qs = urllib.parse.parse_qs(parsed.query)
        code = (qs.get("code") or [""])[0]
        if code:
            return str(code)
    return s


def _paths_match(request_path: str, expected_path: str) -> bool:
    req = request_path or "/"
    exp = expected_path or "/"
    return req == exp or req.rstrip("/") == exp.rstrip("/")


def _wait_for_local_oauth_callback(
    redirect_uri: str,
    *,
    expected_state: str,
    open_url: str,
    timeout_sec: int = 300,
) -> str:
    """Bind to the configured localhost redirect URI and wait for Google's callback."""
    parsed = urllib.parse.urlparse(redirect_uri)
    host = (parsed.hostname or "localhost").lower()
    if host not in _LOCAL_CALLBACK_HOSTS:
        raise ValueError(f"redirect_uri host is not local: {redirect_uri!r}")

    port = parsed.port
    if port is None:
        port = 443 if parsed.scheme == "https" else 80
    expected_path = parsed.path or "/"

    result: dict[str, str | None] = {"code": None, "error": None}
    done = Event()

    class _OAuthHandler(BaseHTTPRequestHandler):
        def log_message(self, format: str, *args: Any) -> None:
            return

        def do_GET(self) -> None:
            req = urllib.parse.urlparse(self.path)
            if not _paths_match(req.path, expected_path):
                self.send_response(404)
                self.end_headers()
                return
            qs = urllib.parse.parse_qs(req.query)
            state = (qs.get("state") or [""])[0]
            if state != expected_state:
                result["error"] = "OAuth state mismatch"
            elif qs.get("error"):
                result["error"] = (qs.get("error") or ["unknown"])[0]
            else:
                result["code"] = (qs.get("code") or [""])[0] or None
            self.send_response(200)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.end_headers()
            self.wfile.write(
                b"<html><body><p>GA4 authorization complete. "
                b"You can close this tab and return to the terminal.</p></body></html>"
            )
            done.set()

    server = HTTPServer((host, port), _OAuthHandler)
    server.timeout = 1

    print(f"Opening browser for Google sign-in (redirect: {redirect_uri}) …")
    webbrowser.open(open_url)
    print(f"Waiting for OAuth callback on {redirect_uri} (timeout {timeout_sec}s) …")

    deadline = time.time() + timeout_sec
    while not done.is_set() and time.time() < deadline:
        server.handle_request()

    server.server_close()

    if result["error"]:
        raise RuntimeError(f"OAuth failed: {result['error']}")
    if not result["code"]:
        raise RuntimeError(
            f"Timed out waiting for OAuth callback on {redirect_uri}. "
            "If the redirect URI is remote (e.g. ngrok), re-run with --auth-code "
            "and paste the code from the redirect URL."
        )
    return str(result["code"])


def _prompt_for_oauth_code(redirect_uri: str, authorize_url: str) -> str:
    print(
        "\nOpen this URL in your browser and sign in with Google:\n"
        f"{authorize_url}\n"
    )
    print(
        "After consent, Google redirects to your configured redirect URI:\n"
        f"  {redirect_uri}\n"
        "Copy the full redirect URL from the browser address bar "
        "(or paste only the code= value) and press Enter:\n"
    )
    raw = input("> ").strip()
    return _extract_oauth_code(raw)


def run_web_oauth_login(
    client_id: str,
    client_secret: str,
    redirect_uri: str,
    *,
    auth_code: str | None = None,
    timeout_sec: int = 300,
) -> Credentials:
    """
    Same web OAuth flow as Streamlit / ``/api/ga4/login``: consent URL → code → token.

    When ``redirect_uri`` points at localhost, a one-shot local server receives the callback.
    Otherwise the user is prompted to paste the redirect URL (e.g. ngrok / deployed host).
    """
    if auth_code:
        code = _extract_oauth_code(auth_code)
        return exchange_code(client_id, client_secret, redirect_uri, code)

    flow = build_flow(client_id, client_secret, redirect_uri)
    state = new_oauth_state()
    authorize_url = authorization_url(flow, state=state)

    parsed = urllib.parse.urlparse(redirect_uri)
    host = (parsed.hostname or "").lower()
    if host in _LOCAL_CALLBACK_HOSTS:
        try:
            code = _wait_for_local_oauth_callback(
                redirect_uri,
                expected_state=state,
                open_url=authorize_url,
                timeout_sec=timeout_sec,
            )
        except OSError as exc:
            raise RuntimeError(
                f"Could not listen on {redirect_uri} ({exc}). "
                "Stop any app using that port, or set GA4_OAUTH_REDIRECT_URI to a free localhost URL."
            ) from exc
    else:
        code = _prompt_for_oauth_code(redirect_uri, authorize_url)

    return exchange_code(client_id, client_secret, redirect_uri, code)


def acquire_cli_credentials(
    *,
    token_path: Path | None = None,
    secrets_path: Path | None = None,
    force_login: bool = False,
    auth_code: str | None = None,
    timeout_sec: int = 300,
) -> Credentials:
    """
    Load cached OAuth credentials or run the same web OAuth login as the deployed app.

    Saves tokens to ``token_path`` (default: ``research/.ga4_oauth_token.json``).
    """
    path = (token_path or _DEFAULT_CLI_TOKEN_PATH).expanduser().resolve()
    if not force_login and not auth_code:
        cached = load_adc_credentials(path)
        if cached is not None:
            return ensure_fresh_credentials(cached)

    cfg = load_oauth_config(secrets_path)
    if cfg is None:
        raise RuntimeError(
            "GA4 OAuth client not configured. Add [ga4_oauth] or [auth] to "
            f"{_DEFAULT_SECRETS_PATH} (see .streamlit/secrets.toml.example), or set "
            "GA4_OAUTH_CLIENT_ID / GA4_OAUTH_CLIENT_SECRET / GA4_OAUTH_REDIRECT_URI."
        )

    creds = run_web_oauth_login(
        cfg.client_id,
        cfg.client_secret,
        cfg.redirect_uri,
        auth_code=auth_code,
        timeout_sec=timeout_sec,
    )
    save_adc_credentials(creds, path)
    print(f"Saved OAuth token to {path}")
    return creds


def install_oauth_application_default_credentials(
    creds: Credentials,
    *,
    token_path: Path | None = None,
) -> Path:
    """
    Persist credentials and point ``GOOGLE_APPLICATION_CREDENTIALS`` at them
    so ``ga4_fetch`` / ``ga4_data_api`` pick up the same user session.
    """
    path = save_adc_credentials(
        ensure_fresh_credentials(creds),
        token_path or _DEFAULT_CLI_TOKEN_PATH,
    )
    os.environ["GOOGLE_APPLICATION_CREDENTIALS"] = str(path)
    return path


def _web_client_config(client_id: str, client_secret: str, redirect_uri: str) -> dict[str, Any]:
    ru = redirect_uri.strip()
    return {
        "web": {
            "client_id": client_id.strip(),
            "client_secret": client_secret.strip(),
            "auth_uri": "https://accounts.google.com/o/oauth2/auth",
            "token_uri": "https://oauth2.googleapis.com/token",
            "redirect_uris": [ru],
        }
    }


def build_flow(client_id: str, client_secret: str, redirect_uri: str) -> Any:
    """Return a ``google_auth_oauthlib.flow.Flow`` for installed/web config."""
    from google_auth_oauthlib.flow import Flow

    cfg = _web_client_config(client_id, client_secret, redirect_uri)
    # Web client + client_secret: do not use PKCE. Otherwise the auth step sends
    # code_challenge but token exchange uses a new Flow without the verifier → invalid_grant.
    return Flow.from_client_config(
        cfg,
        scopes=list(GA4_OAUTH_SCOPES),
        redirect_uri=redirect_uri.strip(),
        autogenerate_code_verifier=False,
    )


def authorization_url(flow: Any, *, state: str) -> str:
    """URL to send the user to (includes offline access for refresh token)."""
    url, _ = flow.authorization_url(
        access_type="offline",
        prompt="consent",
        state=state,
        include_granted_scopes="true",
    )
    return str(url)


def exchange_code(
    client_id: str,
    client_secret: str,
    redirect_uri: str,
    code: str,
) -> Credentials:
    """Exchange the auth ``code`` query param for user credentials."""
    flow = build_flow(client_id, client_secret, redirect_uri)
    # Dedicated GA4-only clients may still return a subset of requested scopes.
    _prev_relax = os.environ.get("OAUTHLIB_RELAX_TOKEN_SCOPE")
    os.environ["OAUTHLIB_RELAX_TOKEN_SCOPE"] = "1"
    try:
        flow.fetch_token(code=code)
    finally:
        if _prev_relax is None:
            os.environ.pop("OAUTHLIB_RELAX_TOKEN_SCOPE", None)
        else:
            os.environ["OAUTHLIB_RELAX_TOKEN_SCOPE"] = _prev_relax
    creds: Credentials = flow.credentials
    if not creds.refresh_token:
        raise RuntimeError(
            "Google did not return a refresh token. Try **Disconnect** then connect again, "
            "or remove the app at https://myaccount.google.com/permissions and retry."
        )
    return creds


def credentials_to_dict(creds: Credentials) -> dict[str, Any]:
    return {
        "token": creds.token,
        "refresh_token": creds.refresh_token,
        "token_uri": creds.token_uri or "https://oauth2.googleapis.com/token",
        "client_id": creds.client_id,
        "client_secret": creds.client_secret,
        "scopes": list(creds.scopes or list(GA4_OAUTH_SCOPES)),
    }


def credentials_from_dict(data: dict[str, Any]) -> Credentials:
    return Credentials(
        token=data.get("token"),
        refresh_token=data.get("refresh_token"),
        token_uri=data.get("token_uri") or "https://oauth2.googleapis.com/token",
        client_id=str(data["client_id"]),
        client_secret=str(data["client_secret"]),
        scopes=tuple(data.get("scopes") or GA4_OAUTH_SCOPES),
    )


def ensure_fresh_credentials(creds: Credentials) -> Credentials:
    """Refresh access token if expired or missing."""
    if not creds.valid and creds.refresh_token:
        creds.refresh(Request())
    return creds


def write_temp_application_default_user_json(creds: Credentials) -> Path:
    """
    Write ``authorized_user`` JSON so ``google.auth.default()`` in subprocesses
    picks up the same credentials (``GOOGLE_APPLICATION_CREDENTIALS``).
    """
    creds = ensure_fresh_credentials(creds)
    if not creds.client_id or not creds.client_secret or not creds.refresh_token:
        raise ValueError("Credentials missing client_id, client_secret, or refresh_token")

    payload: dict[str, Any] = {
        "type": "authorized_user",
        "client_id": creds.client_id,
        "client_secret": creds.client_secret,
        "refresh_token": creds.refresh_token,
    }
    if creds.token:
        payload["token"] = creds.token

    fd, path_str = tempfile.mkstemp(prefix="ga4_user_adc_", suffix=".json", dir=None, text=False)
    path = Path(path_str)
    try:
        os.chmod(path, 0o600)
        os.write(fd, (json.dumps(payload, indent=2) + "\n").encode("utf-8"))
    finally:
        os.close(fd)
    return path


def list_ga4_properties(creds: Credentials) -> list[dict[str, str]]:
    """
    Return accessible GA4 properties via Admin API ``accountSummaries:list``.

    Each item: ``id``, ``name``, ``account`` (display name), ``account_id`` (numeric id).
    """
    creds = ensure_fresh_credentials(creds)
    session = AuthorizedSession(creds)
    out: list[dict[str, str]] = []
    url = "https://analyticsadmin.googleapis.com/v1beta/accountSummaries"
    while url:
        r = session.get(url, timeout=60)
        r.raise_for_status()
        data = r.json()
        for summary in data.get("accountSummaries") or []:
            account_resource = str(summary.get("account") or "").strip()
            account_id = ""
            if account_resource.startswith("accounts/"):
                account_id = account_resource.split("/", 1)[-1].strip()
            account_label = str(summary.get("displayName") or account_id or account_resource).strip()
            if not account_id and account_label:
                account_id = account_label
            for ps in summary.get("propertySummaries") or []:
                prop_resource = str(ps.get("property") or "").strip()
                if not prop_resource.startswith("properties/"):
                    continue
                pid = prop_resource.split("/", 1)[-1].strip()
                if not pid.isdigit():
                    continue
                pname = str(ps.get("displayName") or pid).strip()
                out.append(
                    {
                        "id": pid,
                        "name": pname,
                        "account": account_label,
                        "account_id": account_id,
                    }
                )
        token = data.get("nextPageToken")
        if token:
            url = (
                "https://analyticsadmin.googleapis.com/v1beta/accountSummaries?pageToken="
                + urllib.parse.quote(str(token), safe="")
            )
        else:
            break
    out.sort(key=lambda x: (x.get("name", "").lower(), x["id"]))
    return out


def accounts_from_property_rows(rows: list[dict[str, str]]) -> list[dict[str, str]]:
    """Distinct GA4 accounts (alphabetical by display name) derived from property rows."""
    seen: set[str] = set()
    accounts: list[dict[str, str]] = []
    for row in rows:
        aid = str(row.get("account_id") or "").strip()
        if not aid or aid in seen:
            continue
        seen.add(aid)
        accounts.append(
            {
                "id": aid,
                "name": str(row.get("account") or aid).strip() or aid,
            }
        )
    accounts.sort(key=lambda x: x.get("name", "").lower())
    return accounts


def list_ga4_accounts(creds: Credentials) -> list[dict[str, str]]:
    return accounts_from_property_rows(list_ga4_properties(creds))


def new_oauth_state() -> str:
    return secrets.token_urlsafe(24)


def fetch_top_pages_last_90_days(
    creds: Credentials,
    property_id: str,
    *,
    limit: int = 100,
) -> list[dict[str, Any]]:
    """
    Top pages by ``screenPageViews`` for the last 90 days (exclusive of today in GA4-relative terms).

    Each row: ``host``, ``path`` (no query), ``title``, ``pageviews``, ``full_url``.
    """
    from google.analytics.data_v1beta import BetaAnalyticsDataClient
    from google.analytics.data_v1beta.types import (
        DateRange,
        Dimension,
        Metric,
        OrderBy,
        RunReportRequest,
    )

    creds = ensure_fresh_credentials(creds)
    pid = property_id.strip()
    if pid.startswith("properties/"):
        pid = pid.split("/", 1)[-1]
    if not pid.isdigit():
        raise ValueError(f"Invalid GA4 property id: {property_id!r}")

    client = BetaAnalyticsDataClient(credentials=creds)
    request = RunReportRequest(
        property=f"properties/{pid}",
        dimensions=[
            Dimension(name="hostName"),
            Dimension(name="pagePathPlusQueryString"),
            Dimension(name="pageTitle"),
        ],
        metrics=[Metric(name="screenPageViews")],
        date_ranges=[DateRange(start_date="90daysAgo", end_date="yesterday")],
        order_bys=[
            OrderBy(
                desc=True,
                metric=OrderBy.MetricOrderBy(metric_name="screenPageViews"),
            )
        ],
        limit=min(max(limit, 1), 250),
    )
    response = client.run_report(request)
    out: list[dict[str, Any]] = []
    for row in response.rows or []:
        dv = row.dimension_values
        mv = row.metric_values
        if len(dv) < 3 or not mv:
            continue
        host = (dv[0].value or "").strip()
        raw_path = (dv[1].value or "").strip()
        title = (dv[2].value or "").strip()
        try:
            views = int(float(mv[0].value or 0))
        except ValueError:
            views = 0
        page_path = raw_path.split("?")[0] if raw_path else "/"
        if not page_path.startswith("/"):
            page_path = "/" + page_path
        scheme = "https"
        full_url = f"{scheme}://{host}{page_path}" if host else page_path
        out.append(
            {
                "host": host,
                "path": page_path,
                "title": title or "(not set)",
                "pageviews": views,
                "full_url": full_url,
            }
        )
    return out


def fetch_top_country_by_users_last_90_days(
    creds: Credentials,
    property_id: str,
) -> dict[str, Any] | None:
    """
    Top **country** by **activeUsers** (GA4’s user-based activity metric) for the last 90 days.

    Returns ``{"country": "United Kingdom", "country_id": "GB", "active_users": 12345}`` or ``None`` if
    there is no usable row (e.g. only ``(not set)``). ``country_id`` is ISO 3166-1 alpha-2 when GA4 returns it.
    """
    from google.analytics.data_v1beta import BetaAnalyticsDataClient
    from google.analytics.data_v1beta.types import (
        DateRange,
        Dimension,
        Metric,
        OrderBy,
        RunReportRequest,
    )

    creds = ensure_fresh_credentials(creds)
    pid = property_id.strip()
    if pid.startswith("properties/"):
        pid = pid.split("/", 1)[-1]
    if not pid.isdigit():
        raise ValueError(f"Invalid GA4 property id: {property_id!r}")

    client = BetaAnalyticsDataClient(credentials=creds)
    base_kwargs: dict[str, Any] = dict(
        property=f"properties/{pid}",
        metrics=[Metric(name="activeUsers")],
        date_ranges=[DateRange(start_date="90daysAgo", end_date="yesterday")],
        order_bys=[
            OrderBy(
                desc=True,
                metric=OrderBy.MetricOrderBy(metric_name="activeUsers"),
            )
        ],
        limit=25,
    )
    for dims in (
        [Dimension(name="country"), Dimension(name="countryId")],
        [Dimension(name="country")],
    ):
        try:
            request = RunReportRequest(dimensions=dims, **base_kwargs)
            response = client.run_report(request)
        except Exception:
            continue
        for row in response.rows or []:
            dv = row.dimension_values
            mv = row.metric_values
            if len(dv) < 1 or not mv:
                continue
            country = (dv[0].value or "").strip()
            if not country or country in ("(not set)", "not set"):
                continue
            country_id = ""
            if len(dims) > 1 and len(dv) > 1:
                country_id = (dv[1].value or "").strip()
            try:
                users = int(float(mv[0].value or 0))
            except ValueError:
                users = 0
            return {
                "country": country,
                "country_id": country_id,
                "active_users": users,
            }
    return None
