"""Celery tasks. Each is idempotent and guarded by a Redis lock."""

from __future__ import annotations

import socket
import time

from app.core.db import session_scope
from app.core.logging import get_logger
from app.workers.celery_app import celery_app
from app.workers.locks import redis_client, single_instance

log = get_logger(__name__)

RETRY = dict(autoretry_for=(Exception,), retry_backoff=60, retry_backoff_max=900, retry_jitter=True, max_retries=3)


@celery_app.task(**RETRY)
@single_instance("ingest_gdelt_events", ttl_seconds=1800)
def ingest_gdelt_events() -> dict:
    from app.ingestion.connectors.gdelt_events import GdeltEventsConnector

    with session_scope() as s:
        stats = GdeltEventsConnector(s).run()
    if stats.get("inserted"):
        monitoring_pipeline.delay()  # event-driven: cluster/score/alert right after new data
    return stats


@celery_app.task(**RETRY)
@single_instance("ingest_gdelt_news", ttl_seconds=1800)
def ingest_gdelt_news() -> dict:
    from app.ingestion.connectors.gdelt_doc import GdeltDocConnector

    with session_scope() as s:
        return GdeltDocConnector(s).run()


@celery_app.task(**RETRY)
@single_instance("monitoring_pipeline", ttl_seconds=1200)
def monitoring_pipeline() -> dict:
    from app.workers.pipeline import run_monitoring_pipeline

    out = run_monitoring_pipeline()
    if out.get("alerts_created"):
        ai_analyze_active_crises.delay(trigger="event")
    return out


@celery_app.task(max_retries=1)
@single_instance("ai_analyze_active_crises", ttl_seconds=3000)
def ai_analyze_active_crises(trigger: str = "scheduled") -> dict:
    from app.ai.service import analyze_top_crises

    with session_scope() as s:
        return analyze_top_crises(s, trigger=trigger)


@celery_app.task(max_retries=1)
@single_instance("ai_daily_report", ttl_seconds=3000)
def ai_daily_report() -> dict:
    from app.ai.service import daily_report

    with session_scope() as s:
        a = daily_report(s)
        return {"status": a.status if a else "unavailable"}


@celery_app.task(**RETRY)
@single_instance("ml_feature_snapshots", ttl_seconds=3600)
def ml_feature_snapshots() -> dict:
    from app.ml.features import build_daily_snapshots

    with session_scope() as s:
        return build_daily_snapshots(s)


@celery_app.task(**RETRY)
@single_instance("ucdp_check_release", ttl_seconds=7200)
def ucdp_check_release() -> dict:
    from app.ingestion.connectors.ucdp import UcdpGedConnector

    with session_scope() as s:
        return UcdpGedConnector(s).check_and_import_configured()


@celery_app.task
def heartbeat() -> dict:
    """Worker liveness for the Monitoring Status panel."""
    r = redis_client()
    payload = {"host": socket.gethostname(), "ts": time.time()}
    r.hset("georisk:workers", payload["host"], payload["ts"])
    return payload


@celery_app.task(**RETRY)
@single_instance("news_daily_archive", ttl_seconds=3600)
def news_daily_archive() -> dict:
    """Daily: the newest GDELT 1.0 daily files (one small file per day). Keeps the 30-day 'violent news compared
    with normal' signal used for crisis severity current; already downloaded files are skipped."""
    from datetime import date

    from app.ml.news_history import backfill

    today = date.today()
    return backfill(today.year - 1 if today.month == 1 else today.year, today.year, workers=1)


@celery_app.task(**RETRY)
@single_instance("ml_monthly_early_warning", ttl_seconds=10800)
def ml_monthly_early_warning() -> dict:
    """Monthly: newest UCDP Candidate data -> current-year news archive -> track record of past predictions ->
    retrain the outlook models (kept only if at least as good) -> early-warning predictions."""
    from datetime import date

    from app.ingestion.connectors.ucdp import UcdpGedConnector
    from app.ml import retrain, track_record
    from app.ml.news_history import backfill
    from app.ml.predictions import run_predictions

    out: dict = {}
    with session_scope() as s:
        out["ucdp_candidate"] = UcdpGedConnector(s).import_latest_candidate()
    out["news"] = backfill(date.today().year, date.today().year, workers=2)
    for step, fn in (("track_record", track_record.update), ("retraining", retrain.retrain_if_better)):
        try:  # a failed check or retrain must never block this month's predictions
            with session_scope() as s:
                res = fn(s)
            out[step] = {n: e.get("replaced", e.get("roc_auc")) for n, e in res.get("models", {}).items()}
        except Exception as exc:
            log.exception("monthly_step_failed", step=step)
            out[step] = {"error": str(exc)}
    with session_scope() as s:
        out["predictions"] = run_predictions(s)
    return out


@celery_app.task
@single_instance("ml_ensure_early_warning", ttl_seconds=4 * 3600)
def ml_ensure_early_warning() -> dict:
    """Runs the first-time early-warning setup when no predictions exist yet (cheap no-op otherwise)."""
    from app.ml.predictions import bootstrap_early_warning, needs_bootstrap

    with session_scope() as s:
        if not needs_bootstrap(s):
            return {"status": "not_needed"}
    return bootstrap_early_warning()


from celery.signals import worker_ready  # noqa: E402


@worker_ready.connect
def _kick_off_early_warning_setup(**_kwargs) -> None:
    ml_ensure_early_warning.apply_async(countdown=30, queue="ml")
