"""
alerting.py — webhook and email alerting for LocallyAI watchdog events.

Configure via environment variables in .env:
  LOCALLYAI_ALERT_WEBHOOK_URL  — HTTP POST endpoint (Slack, Teams, PagerDuty, etc.)
  LOCALLYAI_SMTP_HOST          — SMTP server hostname
  LOCALLYAI_SMTP_PORT          — SMTP port (default 587)
  LOCALLYAI_SMTP_USER          — SMTP login username
  LOCALLYAI_SMTP_PASS          — SMTP login password
  LOCALLYAI_ALERT_EMAIL        — destination email address
  LOCALLYAI_DEPLOYMENT_ID      — human-readable name for this deployment (e.g. "SmithCo-LLP")
"""
import json
import logging
import os
import smtplib
import urllib.request
from datetime import datetime, timezone
from email.mime.text import MIMEText

log = logging.getLogger("alerting")

_WEBHOOK_URL   = os.environ.get("LOCALLYAI_ALERT_WEBHOOK_URL", "")
_SMTP_HOST     = os.environ.get("LOCALLYAI_SMTP_HOST", "")
_SMTP_PORT     = int(os.environ.get("LOCALLYAI_SMTP_PORT", "587"))
_SMTP_USER     = os.environ.get("LOCALLYAI_SMTP_USER", "")
_SMTP_PASS     = os.environ.get("LOCALLYAI_SMTP_PASS", "")
_ALERT_EMAIL   = os.environ.get("LOCALLYAI_ALERT_EMAIL", "")
_DEPLOYMENT_ID = os.environ.get("LOCALLYAI_DEPLOYMENT_ID", "locallyai")


def _send_webhook(message: str, level: str) -> None:
    if not _WEBHOOK_URL:
        return
    payload = json.dumps({
        "deployment": _DEPLOYMENT_ID,
        "level": level,
        "message": message,
        "timestamp": datetime.now(timezone.utc).isoformat(),
    }).encode()
    try:
        req = urllib.request.Request(
            _WEBHOOK_URL,
            data=payload,
            headers={"Content-Type": "application/json"},
        )
        urllib.request.urlopen(req, timeout=10)
    except Exception as exc:
        log.error(f"Webhook send failed: {exc}")


def _send_email(subject: str, body: str) -> None:
    if not all([_SMTP_HOST, _SMTP_USER, _SMTP_PASS, _ALERT_EMAIL]):
        return
    msg = MIMEText(body)
    msg["Subject"] = f"[LocallyAI/{_DEPLOYMENT_ID}] {subject}"
    msg["From"]    = _SMTP_USER
    msg["To"]      = _ALERT_EMAIL
    try:
        with smtplib.SMTP(_SMTP_HOST, _SMTP_PORT, timeout=10) as s:
            s.starttls()
            s.login(_SMTP_USER, _SMTP_PASS)
            s.sendmail(_SMTP_USER, [_ALERT_EMAIL], msg.as_string())
    except Exception as exc:
        log.error(f"Email send failed: {exc}")


def send_alert(message: str, level: str = "info") -> None:
    """
    Send an alert via webhook and/or email.
    level: "info" | "warning" | "critical"
    Email is only sent for warning and critical to reduce noise.
    """
    log.info(f"Alert [{level}]: {message}")
    _send_webhook(message, level)
    if level in ("warning", "critical"):
        _send_email(f"{level.upper()}: {message[:80]}", message)
