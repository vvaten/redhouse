"""Alert mail for the controller timers; staging only logs."""

import os

from src.common.logger import setup_logger
from src.monitoring.email_sender import send_alert_email

logger = setup_logger(__name__)


def is_staging(config) -> bool:
    """Staging by its flag or by its buckets, so a lost flag still mails nothing."""
    flag = os.getenv("STAGING_MODE", "false").lower() in ("true", "1", "yes")
    return flag or config.influxdb_bucket_load_control.endswith("_staging")


def mailer(config, staging: bool):
    """A mail(subject, body) -> bool function."""

    def mail(subject: str, body: str) -> bool:
        if staging:
            logger.warning(f"Alert (staging, not mailed): {subject}: {body}")
            return True
        api_key = config.get("RESEND_API_KEY")
        to_email = config.get("ALERT_EMAIL_TO")
        if not api_key or not to_email:
            logger.error(f"Cannot mail {subject}: no email settings")
            return False
        from_email = config.get("ALERT_EMAIL_FROM")
        kwargs = {"from_email": from_email} if from_email else {}
        return bool(send_alert_email(api_key, to_email, subject, body, **kwargs))

    return mail
