"""Audit completion email notifications.

Sends a simple HTML email when an audit finishes. Requires either:
  - SENDGRID_API_KEY env var (preferred on Cloud Run)
  - SMTP_HOST / SMTP_PORT / SMTP_USER / SMTP_PASS env vars for SMTP relay

If neither is configured the function logs and returns False silently.
"""
from __future__ import annotations

import json
import logging
import os
import smtplib
import urllib.request
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText

log = logging.getLogger(__name__)

REPORT_BASE_URL = os.getenv(
    "REPORT_BASE_URL",
    "https://geo-audit-dev-536527023341.europe-west1.run.app",
).rstrip("/")

FROM_EMAIL = os.getenv("NOTIFICATION_FROM_EMAIL", "GEO Audit <noreply@geo-audit.app>")
SENDGRID_API_KEY = os.getenv("SENDGRID_API_KEY", "")
SMTP_HOST = os.getenv("SMTP_HOST", "")
SMTP_PORT = int(os.getenv("SMTP_PORT", "587"))
SMTP_USER = os.getenv("SMTP_USER", "")
SMTP_PASS = os.getenv("SMTP_PASS", "")


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


def _send_via_sendgrid(to_email: str, subject: str, html: str, text: str) -> bool:
    payload = json.dumps(
        {
            "personalizations": [{"to": [{"email": to_email}]}],
            "from": {"email": FROM_EMAIL.split("<")[-1].strip(" >")},
            "subject": subject,
            "content": [
                {"type": "text/plain", "value": text},
                {"type": "text/html", "value": html},
            ],
        }
    ).encode()
    req = urllib.request.Request(
        "https://api.sendgrid.com/v3/mail/send",
        data=payload,
        headers={
            "Authorization": f"Bearer {SENDGRID_API_KEY}",
            "Content-Type": "application/json",
        },
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=10) as resp:
            return resp.status in (200, 202)
    except Exception as exc:
        log.warning("SendGrid send failed: %s", exc)
        return False


def _send_via_smtp(to_email: str, subject: str, html: str, text: str) -> bool:
    msg = MIMEMultipart("alternative")
    msg["Subject"] = subject
    msg["From"] = FROM_EMAIL
    msg["To"] = to_email
    msg.attach(MIMEText(text, "plain"))
    msg.attach(MIMEText(html, "html"))
    try:
        with smtplib.SMTP(SMTP_HOST, SMTP_PORT, timeout=10) as server:
            server.ehlo()
            server.starttls()
            if SMTP_USER and SMTP_PASS:
                server.login(SMTP_USER, SMTP_PASS)
            server.sendmail(FROM_EMAIL, [to_email], msg.as_string())
        return True
    except Exception as exc:
        log.warning("SMTP send failed: %s", exc)
        return False


def send_audit_complete_email(
    to_email: str,
    brand_name: str,
    audit_dir: str,
) -> bool:
    """Send audit-complete notification email. Returns True on success."""
    if not to_email or "@" not in to_email:
        return False

    report_url = _report_url(audit_dir)
    brand_label = brand_name.strip() or "Your"
    subject = f"{brand_label} GEO audit is ready"
    html = _html_body(brand_label, report_url)
    text = _text_body(brand_label, report_url)

    if SENDGRID_API_KEY:
        ok = _send_via_sendgrid(to_email, subject, html, text)
        if ok:
            log.info("Audit completion email sent via SendGrid to %s", to_email)
        return ok

    if SMTP_HOST:
        ok = _send_via_smtp(to_email, subject, html, text)
        if ok:
            log.info("Audit completion email sent via SMTP to %s", to_email)
        return ok

    log.info(
        "No email provider configured — skipping notification to %s. "
        "Set SENDGRID_API_KEY or SMTP_HOST to enable.",
        to_email,
    )
    return False
