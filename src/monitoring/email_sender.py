"""Email alert sender using Resend API."""

import json
import urllib.error
import urllib.request
from typing import Optional

from src.common.logger import setup_logger

logger = setup_logger(__name__, "health_check.log")

RESEND_API_URL = "https://api.resend.com/emails"


def send_alert_email(
    api_key: str,
    to_email: str,
    subject: str,
    body: str,
    from_email: str = "RedHouse <alerts@resend.dev>",
) -> bool:
    """Send an alert email via Resend API.

    Args:
        api_key: Resend API key
        to_email: Recipient email address
        subject: Email subject
        body: Plain text email body
        from_email: Sender address (must be verified in Resend)

    Returns:
        True if email was sent successfully
    """
    payload = json.dumps(
        {
            "from": from_email,
            "to": [to_email],
            "subject": subject,
            "text": body,
        }
    ).encode("utf-8")

    req = urllib.request.Request(
        RESEND_API_URL,
        data=payload,
        headers={
            "Authorization": f"Bearer {api_key}",
            "Content-Type": "application/json",
            "User-Agent": "RedHouse/1.0",
        },
        method="POST",
    )

    try:
        with urllib.request.urlopen(req, timeout=30) as resp:
            if 200 <= resp.status < 300:
                logger.info("Alert email sent (HTTP %d): %s", resp.status, subject)
                return True
            logger.error("Resend API returned status %d", resp.status)
            return False
    except urllib.error.HTTPError as e:
        error_body = e.read().decode("utf-8", errors="replace")
        logger.error("Resend API error %d: %s", e.code, error_body)
        return False
    except (urllib.error.URLError, OSError) as e:
        logger.error("Failed to send alert email: %s", e)
        return False


def format_alert_body(
    hostname: str, failures: list[str], warnings: Optional[list[str]] = None
) -> str:
    """Format a health check alert email body.

    Args:
        hostname: Machine hostname
        failures: List of failure messages
        warnings: Optional list of warning messages

    Returns:
        Formatted email body text
    """
    lines = [f"Health check alert from {hostname}", ""]

    if failures:
        lines.append("FAILURES:")
        for f in failures:
            lines.append(f"  - {f}")
        lines.append("")

    if warnings:
        lines.append("WARNINGS:")
        for w in warnings:
            lines.append(f"  - {w}")
        lines.append("")

    return "\n".join(lines)
