"""Celery application and Beat schedule (all intervals configurable)."""

from __future__ import annotations

from celery import Celery
from celery.schedules import crontab

from app.core.config import get_settings

settings = get_settings()

celery_app = Celery("georisk", broker=settings.redis_url, backend=settings.redis_url, include=["app.workers.tasks"])
celery_app.conf.update(
    task_acks_late=True,
    worker_prefetch_multiplier=1,
    task_reject_on_worker_lost=True,
    task_default_queue="default",
    task_routes={"app.workers.tasks.ai_*": {"queue": "ai"}, "app.workers.tasks.ml_*": {"queue": "ml"}},
    result_expires=86400,
    timezone="UTC",
    enable_utc=True,
    broker_connection_retry_on_startup=True,
    beat_schedule={
        "gdelt-events": {"task": "app.workers.tasks.ingest_gdelt_events",
                         "schedule": settings.gdelt_events_interval_minutes * 60},
        "gdelt-news": {"task": "app.workers.tasks.ingest_gdelt_news", "schedule": settings.gdelt_doc_interval_minutes * 60},
        "monitoring-pipeline": {"task": "app.workers.tasks.monitoring_pipeline",
                                "schedule": settings.priority_interval_minutes * 60},
        "ai-crisis-analysis": {"task": "app.workers.tasks.ai_analyze_active_crises",
                               "schedule": settings.ai_crisis_interval_minutes * 60},
        "ai-daily-report": {"task": "app.workers.tasks.ai_daily_report",
                            "schedule": crontab(hour=settings.ai_daily_report_hour_utc, minute=17)},
        "ml-feature-snapshots": {"task": "app.workers.tasks.ml_feature_snapshots",
                                 "schedule": crontab(hour=settings.feature_snapshot_hour_utc, minute=7)},
        "ucdp-release-check": {"task": "app.workers.tasks.ucdp_check_release",
                               "schedule": crontab(day_of_week="mon", hour=3, minute=23)},
        "ml-monthly-early-warning": {"task": "app.workers.tasks.ml_monthly_early_warning",
                                     "schedule": crontab(day_of_month=settings.predictions_day_of_month, hour=4, minute=13)},
        "news-daily-archive": {"task": "app.workers.tasks.news_daily_archive",
                               "schedule": crontab(hour=11, minute=37)},  # GDELT 1.0 daily file is out by ~10:00 UTC
        "ml-ensure-early-warning": {"task": "app.workers.tasks.ml_ensure_early_warning", "schedule": 3600},
        "worker-heartbeat": {"task": "app.workers.tasks.heartbeat", "schedule": 60},
    },
)
