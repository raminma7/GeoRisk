"""Monitoring pipeline: crisis discovery -> indicators -> priorities -> alerts.

Runs after every ingestion cycle and on its own schedule. Each stage commits
independently so a failure in a later stage never loses earlier work.
"""

from __future__ import annotations

from datetime import datetime

from app.core.db import session_scope, utcnow
from app.core.logging import get_logger

log = get_logger(__name__)


def run_monitoring_pipeline(now: datetime | None = None) -> dict:
    from app.crises.engine import CrisisEngine
    from app.geopolitics.indicators import compute_indicators
    from app.monitoring.alerts import evaluate_alerts
    from app.monitoring.priority import score_all

    now = now or utcnow()
    out: dict = {}
    with session_scope() as s:
        out["crises"] = CrisisEngine(s, now=now).run()
    with session_scope() as s:
        out["indicators"] = compute_indicators(s, as_of=now)
    with session_scope() as s:
        priorities = score_all(s, now=now)
        alerts = evaluate_alerts(s, priorities)
        out["priorities"] = len(priorities)
        out["alerts_created"] = len(alerts)
    log.info("monitoring_pipeline_done", **{k: v for k, v in out.items() if not isinstance(v, dict)})
    return out
