"""Alert notification dispatch (structured log + optional webhook)."""

from __future__ import annotations

import httpx
from sqlalchemy.orm import Session

from app.core.config import get_settings
from app.core.logging import get_logger

log = get_logger(__name__)


def dispatch_alert(session: Session, alert) -> None:
    settings = get_settings()
    payload = {
        "id": alert.id, "type": alert.alert_type, "severity": alert.severity, "title": alert.title,
        "crisis_id": alert.crisis_id, "requires_review": alert.requires_review, "created_at": alert.created_at.isoformat(),
    }
    log.info("alert_notification", **payload)
    if settings.alert_webhook_url:
        try:
            httpx.post(settings.alert_webhook_url, json=payload, timeout=10)
            alert.notified = True
        except httpx.HTTPError as exc:
            log.warning("alert_webhook_failed", error=str(exc))
    else:
        alert.notified = True
