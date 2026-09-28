from __future__ import annotations

import smtplib
from email.message import EmailMessage
from typing import Any


def send_alert(summary: dict[str, Any], *, smtp_host: str | None = None, smtp_port: int = 25,
               to_email: str | None = None, from_email: str | None = None) -> bool:
    """Send a lightweight operational alert if email configuration is present."""
    if not smtp_host or not to_email or not from_email:
        return False
    msg = EmailMessage()
    msg["Subject"] = f"QuantRisk operational alert: {summary.get('run_id', 'unknown')}"
    msg["From"] = from_email
    msg["To"] = to_email
    msg.set_content(
        "\n".join([
            f"run_id: {summary.get('run_id', 'unknown')}",
            f"status: {summary.get('status', 'UNKNOWN')}",
            f"stale_files: {', '.join(summary.get('stale_files', [])) or 'none'}",
            f"breaches: {summary.get('breaches', [])}",
        ])
    )
    try:
        with smtplib.SMTP(smtp_host, smtp_port) as server:
            server.send_message(msg)
        return True
    except Exception:
        return False
