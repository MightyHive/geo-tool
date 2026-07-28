"""Audit completion email notifications via the Gmail API.

Preferred local-dev path (send as your Gmail account):
  1. Enable Gmail API + create a Desktop OAuth client in Google Cloud Console
  2. Download the client JSON → ``GMAIL_CLIENT_SECRETS_FILE``
  3. Run ``python -m api.notify_email`` once (browser consent) → writes ``GMAIL_TOKEN_PATH``
  4. Subsequent audit completions send with the cached token (scope: gmail.send)

Optional: set ``GMAIL_USE_ADC=1`` to use Application Default Credentials
(``google.auth.default``) instead of the desktop OAuth files.

If no recipient or no usable credentials, logs and returns False (fail soft).
"""
from __future__ import annotations

import base64
import logging
import os
import re
from email.message import EmailMessage
from pathlib import Path
from typing import Any

log = logging.getLogger(__name__)

GMAIL_SEND_SCOPE = "https://www.googleapis.com/auth/gmail.send"

try:
    from geo_app_env import REPO_ROOT
except Exception:  # pragma: no cover - import path when run as script
    REPO_ROOT = Path(__file__).resolve().parents[1]

# Report link base: explicit override → legacy alias → public app origin → hardcoded default.
REPORT_BASE_URL = (
    os.getenv("REPORT_APP_BASE_URL")
    or os.getenv("REPORT_BASE_URL")
    or os.getenv("WEB_PUBLIC_ORIGIN")
    or "https://geo-audit-dev-536527023341.europe-west1.run.app"
).rstrip("/")

FROM_EMAIL = (
    os.getenv("GMAIL_FROM_EMAIL")
    or os.getenv("NOTIFICATION_FROM_EMAIL")
    or ""
).strip()

_DEFAULT_CLIENT_SECRETS = REPO_ROOT / "env" / "gmail_client_secrets.json"
_DEFAULT_TOKEN = REPO_ROOT / "env" / ".gmail_token.json"

_FROM_ANGLE = re.compile(r"^(?P<name>.*?)\s*<(?P<email>[^>]+)>\s*$")


def _env_path(name: str, default: Path) -> Path:
    raw = (os.getenv(name) or "").strip()
    if not raw:
        return default
    p = Path(raw).expanduser()
    if not p.is_absolute():
        p = (REPO_ROOT / p).resolve()
    return p


def client_secrets_path() -> Path:
    return _env_path("GMAIL_CLIENT_SECRETS_FILE", _DEFAULT_CLIENT_SECRETS)


def token_path() -> Path:
    return _env_path("GMAIL_TOKEN_PATH", _DEFAULT_TOKEN)


def _use_adc() -> bool:
    return (os.getenv("GMAIL_USE_ADC") or "").strip().lower() in {"1", "true", "yes"}


def _parse_from(from_header: str) -> tuple[str, str | None]:
    """Return ``(email, display_name|None)`` from a From header value."""
    raw = (from_header or "").strip()
    m = _FROM_ANGLE.match(raw)
    if m:
        name = m.group("name").strip().strip('"') or None
        return m.group("email").strip(), name
    return raw, None


def _report_url(audit_dir: str) -> str:
    slug = audit_dir.lstrip("/")
    return f"{REPORT_BASE_URL}/report/{slug}/summary"


def _html_body(brand_name: str, report_url: str) -> str:
    name = brand_name or "Your"
    return f"""<!DOCTYPE html>
<html lang="en">
<head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>GEO Audit complete</title>
</head>
<body style="margin:0;padding:0;background:#f5f4f0;font-family:system-ui,sans-serif;color:#1a1a1a;">
<table width="100%" cellpadding="0" cellspacing="0" style="background:#f5f4f0;padding:40px 16px;">
  <tr><td align="center">
    <table width="540" cellpadding="0" cellspacing="0" style="background:#ffffff;border-radius:12px;overflow:hidden;box-shadow:0 1px 3px rgba(0,0,0,.08);">
      <tr>
        <td style="background:#0d0d0d;padding:28px 40px;">
          <p style="margin:0;font-size:13px;font-weight:600;letter-spacing:.08em;color:#a3a3a3;text-transform:uppercase;">GEO Audit</p>
        </td>
      </tr>
      <tr>
        <td style="padding:40px 40px 32px;">
          <h1 style="margin:0 0 12px;font-size:22px;font-weight:700;line-height:1.3;">{name} audit is ready</h1>
          <p style="margin:0 0 28px;font-size:15px;line-height:1.6;color:#555;">
            Your GEO &amp; AI visibility audit has finished. Click the button below to view
            your full report, including AI visibility scores, prompt performance, citations,
            and recommendations.
          </p>
          <a href="{report_url}"
             style="display:inline-block;padding:14px 28px;background:#0d0d0d;color:#ffffff;
                    font-size:14px;font-weight:600;text-decoration:none;border-radius:8px;">
            View report →
          </a>
        </td>
      </tr>
      <tr>
        <td style="padding:0 40px 32px;">
          <hr style="border:none;border-top:1px solid #e5e5e5;margin:0 0 24px;">
          <p style="margin:0;font-size:12px;color:#999;line-height:1.6;">
            Report link: <a href="{report_url}" style="color:#0d0d0d;">{report_url}</a><br>
            This is an automated message. Reply to this email if you have questions.
          </p>
        </td>
      </tr>
    </table>
  </td></tr>
</table>
</body>
</html>"""


def _text_body(brand_name: str, report_url: str) -> str:
    name = brand_name or "Your"
    return (
        f"{name} audit is ready\n\n"
        f"Your GEO & AI visibility audit has finished.\n\n"
        f"View your report: {report_url}\n\n"
        f"This is an automated message."
    )


def _save_token(creds: Any, path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(creds.to_json(), encoding="utf-8")
    try:
        path.chmod(0o600)
    except OSError:
        pass


def _load_oauth_credentials(*, interactive: bool = False) -> Any | None:
    """Load Gmail OAuth user credentials (desktop client + token cache)."""
    from google.auth.transport.requests import Request
    from google.oauth2.credentials import Credentials

    path = token_path()
    secrets = client_secrets_path()
    creds: Any | None = None

    if path.is_file():
        try:
            creds = Credentials.from_authorized_user_file(str(path), [GMAIL_SEND_SCOPE])
        except Exception as exc:
            log.warning("Failed to load Gmail token from %s: %s", path, exc)
            creds = None

    if creds and creds.expired and creds.refresh_token:
        try:
            creds.refresh(Request())
            _save_token(creds, path)
        except Exception as exc:
            log.warning("Gmail token refresh failed: %s", exc)
            creds = None

    if creds and creds.valid:
        return creds

    if not interactive:
        return None

    if not secrets.is_file():
        log.warning(
            "Gmail OAuth interactive login needs client secrets at %s "
            "(set GMAIL_CLIENT_SECRETS_FILE).",
            secrets,
        )
        return None

    from google_auth_oauthlib.flow import InstalledAppFlow

    flow = InstalledAppFlow.from_client_secrets_file(str(secrets), [GMAIL_SEND_SCOPE])
    creds = flow.run_local_server(port=0, prompt="consent")
    _save_token(creds, path)
    log.info("Saved Gmail OAuth token to %s", path)
    return creds


def _load_adc_credentials() -> Any | None:
    """Use Application Default Credentials with gmail.send scope."""
    try:
        import google.auth

        creds, _ = google.auth.default(scopes=[GMAIL_SEND_SCOPE])
        return creds
    except Exception as exc:
        log.warning("Gmail ADC credentials unavailable: %s", exc)
        return None


def get_gmail_credentials(*, interactive: bool = False) -> Any | None:
    """Resolve credentials for Gmail send. Prefer cached OAuth token, then ADC if enabled."""
    if _use_adc():
        return _load_adc_credentials()
    creds = _load_oauth_credentials(interactive=False)
    if creds is not None:
        return creds
    if interactive:
        return _load_oauth_credentials(interactive=True)
    return None


def build_gmail_service(creds: Any | None = None) -> Any | None:
    """Build a Gmail API client, or None if credentials are missing."""
    if creds is None:
        creds = get_gmail_credentials(interactive=False)
    if creds is None:
        return None
    from googleapiclient.discovery import build

    return build("gmail", "v1", credentials=creds, cache_discovery=False)


def _build_raw_message(
    *,
    to_email: str,
    subject: str,
    html: str,
    text: str,
    from_header: str,
) -> str:
    message = EmailMessage()
    message["To"] = to_email
    if from_header:
        message["From"] = from_header
    message["Subject"] = subject
    message.set_content(text)
    message.add_alternative(html, subtype="html")
    return base64.urlsafe_b64encode(message.as_bytes()).decode()


def _send_via_gmail(
    to_email: str,
    subject: str,
    html: str,
    text: str,
    *,
    service: Any | None = None,
) -> bool:
    svc = service or build_gmail_service()
    if svc is None:
        return False

    from_header = FROM_EMAIL
    if not from_header:
        # Let Gmail default From to the authenticated user when unset.
        from_header = ""

    raw = _build_raw_message(
        to_email=to_email,
        subject=subject,
        html=html,
        text=text,
        from_header=from_header,
    )
    try:
        svc.users().messages().send(userId="me", body={"raw": raw}).execute()
        return True
    except Exception as exc:
        # HttpError subclasses Exception; import is optional so unit tests can mock
        # the service without googleapiclient installed in every environment.
        log.warning("Gmail API send failed: %s", exc)
        return False


def send_audit_complete_email(
    to_email: str,
    brand_name: str,
    audit_dir: str,
    *,
    service: Any | None = None,
) -> bool:
    """Send audit-complete notification email via Gmail. Returns True on success."""
    if not to_email or "@" not in to_email:
        return False

    report_url = _report_url(audit_dir)
    brand_label = brand_name.strip() or "Your"
    subject = f"{brand_label} GEO audit is ready"
    html = _html_body(brand_label, report_url)
    text = _text_body(brand_label, report_url)

    # Allow injecting a mock service in tests without touching credentials.
    if service is None:
        if get_gmail_credentials(interactive=False) is None:
            log.info(
                "No Gmail credentials configured — skipping notification to %s. "
                "Run `python -m api.notify_email` once (OAuth browser consent), "
                "or set GMAIL_USE_ADC=1 with ADC that includes gmail.send. "
                "Client secrets: %s · token: %s",
                to_email,
                client_secrets_path(),
                token_path(),
            )
            return False

    ok = _send_via_gmail(to_email, subject, html, text, service=service)
    if ok:
        log.info("Audit completion email sent via Gmail API to %s", to_email)
    return ok


def run_oauth_login() -> Path:
    """Interactive browser OAuth; writes token to ``GMAIL_TOKEN_PATH``. Returns token path."""
    creds = _load_oauth_credentials(interactive=True)
    if creds is None:
        raise SystemExit(
            "Gmail OAuth login failed. Download a Desktop OAuth client JSON from "
            "Google Cloud Console, save it as env/gmail_client_secrets.json "
            "(or set GMAIL_CLIENT_SECRETS_FILE), enable the Gmail API, then retry."
        )
    path = token_path()
    print(f"Gmail send token saved to {path}")
    print(f"Scope: {GMAIL_SEND_SCOPE}")
    return path


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    run_oauth_login()
