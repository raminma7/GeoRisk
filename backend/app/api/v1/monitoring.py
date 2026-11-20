"""Global monitoring: overview, priorities, alerts, status."""

from __future__ import annotations

import time
from datetime import date, timedelta
from typing import Literal

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, Field
from sqlalchemy import desc, func, select, text
from sqlalchemy.orm import Session

from app.ai.models import GeopoliticalAnalysis
from app.api.v1.common import (
    Pagination,
    country_names,
    get_crisis,
    iso,
    paginate,
    region_names,
    ser_cluster,
    ser_crisis_card,
)
from app.core.config import get_settings
from app.core.db import get_db, utcnow
from app.core.security import Principal, require_read, require_role
from app.crises.models import Crisis
from app.geopolitics.models import EventCluster, NormalizedGeopoliticalEvent
from app.ingestion.models import AuditLog, DataSource, IngestionBatch, IngestionRun
from app.monitoring.models import GeopoliticalAlert, MonitoringPriority

router = APIRouter(tags=["Global Monitoring"])
ACTIVE = ["candidate", "active"]


def ser_alert(a: GeopoliticalAlert, crisis_uid: str | None = None) -> dict:
    return {"id": a.id, "type": a.alert_type, "severity": a.severity, "title": a.title, "message": a.message,
            "crisis_id": a.crisis_id, "crisis_uid": crisis_uid, "status": a.status, "requires_review": a.requires_review,
            "created_at": iso(a.created_at), "reviewed_by": a.reviewed_by, "reviewed_at": iso(a.reviewed_at),
            "evidence": a.evidence}


def ser_priority(mp: MonitoringPriority) -> dict:
    return {"id": mp.id, "crisis_id": mp.crisis_id, "score": mp.score, "level": mp.level, "direction": mp.direction,
            "components": mp.components, "triggering_indicators": mp.triggering_indicators,
            "supporting_event_cluster_ids": mp.supporting_event_cluster_ids,
            "independent_source_count": mp.independent_source_count, "evidence_confidence": mp.evidence_confidence,
            "evidence_confidence_label": mp.evidence_confidence_label, "explanation": mp.explanation,
            "algorithm_version": mp.algorithm_version, "weights": mp.weights, "computed_at": iso(mp.computed_at),
            "interpretation": "Review priority for analysts; not a probability of war or a validated risk estimate."}


def _cards(s: Session, limit: int, min_level: str | None = None) -> list[dict]:
    names, regions = country_names(s), region_names(s)
    q = select(Crisis).where(Crisis.status.in_(ACTIVE)).order_by(desc(Crisis.current_priority)).limit(limit)
    return [ser_crisis_card(c, regions.get(c.region_id), names) for c in s.scalars(q)]


def latest_developments(s: Session, hours: int = 24, limit: int = 15) -> list[dict]:
    """Important recent developments: deduplicated, conflict/diplomatic, corroborated first."""
    since = utcnow() - timedelta(hours=hours)
    q = (
        select(EventCluster, NormalizedGeopoliticalEvent.source_url)
        .outerjoin(NormalizedGeopoliticalEvent, NormalizedGeopoliticalEvent.id == EventCluster.representative_event_id)
        .where(EventCluster.kind == "event", EventCluster.last_seen_at >= since,
               EventCluster.event_root_code.in_(["08", "10", "12", "13", "15", "16", "17", "18", "19", "20"]))
        .order_by(desc(EventCluster.max_severity * func.least(func.cardinality(EventCluster.source_domains), 10)))
        .limit(limit)
    )
    return [ser_cluster(cl, url) for cl, url in s.execute(q).all()]


def freshness(s: Session) -> dict:
    out = {}
    for ds in s.scalars(select(DataSource).where(DataSource.enabled.is_(True))):
        last_ok = s.scalar(select(func.max(IngestionRun.finished_at)).where(
            IngestionRun.data_source_id == ds.id, IngestionRun.status.in_(["success", "partial"])))
        expected = ds.expected_interval_minutes
        stale = None
        if expected and last_ok:
            stale = (utcnow() - last_ok) > timedelta(minutes=expected * 3)
        elif expected and not last_ok:
            stale = True
        out[ds.key] = {"last_success_at": iso(last_ok), "stale": stale, "expected_interval_minutes": expected}
    latest_event = s.scalar(select(func.max(NormalizedGeopoliticalEvent.available_at)))
    out["latest_event_available_at"] = iso(latest_event)
    return out


def _early_warning(s: Session) -> dict | None:
    from app.api.v1.predictions import _meta
    from app.ml import predictions as P
    from app.ml.models import MlPrediction
    from app.registry.models import Country

    lw = P.latest_window(s)
    if not lw:
        return None
    rows = s.execute(select(MlPrediction, Country.name).join(Country, Country.iso3 == MlPrediction.iso3)
                     .where(MlPrediction.model_version == lw[0], MlPrediction.window_start == lw[1],
                            MlPrediction.status == "predicted").order_by(MlPrediction.rank).limit(5)).all()
    meta = _meta(s)
    return {"window": meta["window"], "data_through": meta["data_through"], "model_version": lw[0],
            "items": [P.ser(p, n) for p, n in rows]}


@router.get("/overview", summary="Automatic global intelligence overview (no country selection needed)")
def overview(s: Session = Depends(get_db), _: Principal = Depends(require_read)):
    since = utcnow() - timedelta(hours=24)
    counts = {
        "active_crises": s.scalar(select(func.count()).where(Crisis.status.in_(ACTIVE))),
        "crises_needing_review": s.scalar(select(func.count()).where(Crisis.status.in_(ACTIVE), Crisis.needs_review.is_(True))),
        "new_crises_48h": s.scalar(select(func.count()).where(Crisis.status.in_(ACTIVE),
                                                              Crisis.first_observed_at >= utcnow() - timedelta(hours=48))),
        "events_24h": s.scalar(select(func.count()).where(NormalizedGeopoliticalEvent.available_at >= since)),
        "event_clusters_24h": s.scalar(select(func.count()).where(EventCluster.kind == "event", EventCluster.last_seen_at >= since)),
        "countries_with_events_24h": s.scalar(text(
            "SELECT count(DISTINCT action_country_id) FROM normalized_geopolitical_events WHERE available_at >= :s"), {"s": since}),
        "open_alerts": s.scalar(select(func.count()).where(GeopoliticalAlert.status == "open")),
    }
    alerts = s.execute(select(GeopoliticalAlert, Crisis.uid).outerjoin(Crisis, Crisis.id == GeopoliticalAlert.crisis_id)
                       .order_by(desc(GeopoliticalAlert.created_at)).limit(10)).all()
    reports = s.scalars(select(GeopoliticalAnalysis).where(GeopoliticalAnalysis.status == "success")
                        .order_by(desc(GeopoliticalAnalysis.created_at)).limit(5))
    changing = s.scalars(select(Crisis).where(Crisis.status.in_(ACTIVE), Crisis.direction != "stable")
                         .order_by(desc(Crisis.current_priority)).limit(8))
    names, regions = country_names(s), region_names(s)
    return {
        "generated_at": iso(utcnow()),
        "counts": counts,
        "priority_situations": _cards(s, 8),
        "changing_situations": [ser_crisis_card(c, regions.get(c.region_id), names) for c in changing],
        "latest_developments": latest_developments(s),
        "alerts": [ser_alert(a, uid) for a, uid in alerts],
        "latest_reports": [{"id": r.id, "scope_type": r.scope_type, "scope_key": r.scope_key, "created_at": iso(r.created_at),
                            "summary": (r.output or {}).get("summary"), "provider": r.provider, "model": r.model}
                           for r in reports],
        "data_freshness": freshness(s),
        "early_warning": _early_warning(s),
        "ai_enabled": get_settings().ai_provider != "none",
        "disclaimer": "Events are machine-extracted from news reports (GDELT) and are not independently verified. "
                      "Priority scores indicate analyst review priority, not probabilities of conflict.",
    }


@router.get("/today", summary="Plain-language overview for non-specialists: situations, severity, reasons, changes")
def today(s: Session = Depends(get_db), _: Principal = Depends(require_read)):
    from app.monitoring import plain

    now = utcnow()
    sts = plain.active_stories(s, now)
    cs = plain.country_stories(s, sts, now)
    main = [x for x in cs if x["group"] == "main"]
    other = [x for x in cs if x["group"] == "other"]
    ew = _early_warning(s)
    at_risk = 0
    if ew:
        from app.ml import predictions as P

        lv = P.count_by_level(s, ew["model_version"], date.fromisoformat(ew["window"]["start"]))
        at_risk = (lv.get("very_high") or 0) + (lv.get("high") or 0)
    summary = {"countries": len(main), "serious": sum(1 for x in main if x["severity"]["level"] in ("critical", "high")),
               "getting_worse": sum(1 for x in main if x["trend"]["code"] == "up"),
               "calming": sum(1 for x in main if x["trend"]["code"] == "down"), "countries_at_risk": at_risk}
    return {
        "generated_at": iso(now), "summary": summary, "countries": main[:30],
        "other_signals": [{k: x[k] for k in ("iso3", "headline", "severity", "confidence", "reasons")} for x in other[:25]],
        "other_signals_total": len(other),
        "what_changed": plain.what_changed(s, cs, now),
        "could_start_soon": ew,
        "conflict_data_through": iso(plain.deaths_through()),
        "data_freshness": {"latest_event_available_at": freshness(s).get("latest_event_available_at")},
        "how_to_read": {
            "severity": "How serious the situation looks right now, from the number and violence of reported incidents "
                        "and how many news outlets report them (Critical > High > Moderate > Low).",
            "trend": "Compared with the previous days.",
            "confirmed": "How many independent news outlets report it. News reports are extracted automatically and are "
                         "not verified one by one; death counts come from the UCDP research dataset.",
            "other_signals": "Countries with only local reports and no verified conflict deaths (often crime or police "
                             "news), or with very few sources. Shown separately so they don't crowd out real crises.",
            "countries": "A country is listed when crisis events are happening there. Countries that take part from "
                         "outside are shown as 'also involved'.",
        },
    }


@router.get("/monitoring/priorities", summary="Situations ranked by the explainable monitoring priority engine")
def priorities(level: str | None = None, limit: int = Query(25, le=200), s: Session = Depends(get_db),
               _: Principal = Depends(require_read)):
    names, regions = country_names(s), region_names(s)
    q = select(Crisis).where(Crisis.status.in_(ACTIVE))
    if level:
        q = q.where(Crisis.priority_level == level)
    out = []
    for c in s.scalars(q.order_by(desc(Crisis.current_priority)).limit(limit)):
        mp = s.scalar(select(MonitoringPriority).where(MonitoringPriority.crisis_id == c.id)
                      .order_by(desc(MonitoringPriority.id)).limit(1))
        out.append({**ser_crisis_card(c, regions.get(c.region_id), names), "priority_detail": ser_priority(mp) if mp else None})
    return {"items": out}


@router.get("/monitoring/priorities/{crisis_id}/history")
def priority_history(crisis_id: str, limit: int = Query(200, le=2000), s: Session = Depends(get_db),
                     _: Principal = Depends(require_read)):
    c = get_crisis(s, crisis_id)
    rows = s.scalars(select(MonitoringPriority).where(MonitoringPriority.crisis_id == c.id)
                     .order_by(desc(MonitoringPriority.computed_at)).limit(limit))
    return {"crisis_id": c.id, "items": [ser_priority(r) for r in rows]}


@router.get("/monitoring/alerts", summary="Evidence-based monitoring alerts")
def alerts(status: str | None = None, alert_type: str | None = Query(None, alias="type"), crisis_id: int | None = None,
           requires_review: bool | None = None, p: Pagination = Depends(), s: Session = Depends(get_db),
           _: Principal = Depends(require_read)):
    q = select(GeopoliticalAlert, Crisis.uid).outerjoin(Crisis, Crisis.id == GeopoliticalAlert.crisis_id)
    if status:
        q = q.where(GeopoliticalAlert.status == status)
    if alert_type:
        q = q.where(GeopoliticalAlert.alert_type == alert_type)
    if crisis_id:
        q = q.where(GeopoliticalAlert.crisis_id == crisis_id)
    if requires_review is not None:
        q = q.where(GeopoliticalAlert.requires_review.is_(requires_review))
    return paginate(s, q.order_by(desc(GeopoliticalAlert.created_at)), p, lambda r: ser_alert(r[0], r[1]))


class AlertReview(BaseModel):
    status: Literal["acknowledged", "dismissed", "open"]
    note: str | None = Field(None, max_length=2000)


@router.post("/monitoring/alerts/{alert_id}/review", summary="Human review of an alert (analyst)")
def review_alert(alert_id: int, body: AlertReview, s: Session = Depends(get_db),
                 p: Principal = Depends(require_role("analyst"))):
    a = s.get(GeopoliticalAlert, alert_id)
    if a is None:
        raise HTTPException(404, "Alert not found")
    a.status = body.status
    a.reviewed_by = p.name
    a.reviewed_at = utcnow()
    if body.note:
        a.evidence = {**(a.evidence or {}), "review_note": body.note}
    s.add(AuditLog(actor=p.name, action="alert_review", target=f"alert:{a.id}", details=body.model_dump()))
    s.commit()
    return ser_alert(a)


@router.get("/monitoring/status", summary="Data sources, ingestion health, coverage and workers")
def status(s: Session = Depends(get_db), _: Principal = Depends(require_read)):
    sources = []
    for ds in s.scalars(select(DataSource).order_by(DataSource.module, DataSource.id)):
        last = s.scalar(select(IngestionRun).where(IngestionRun.data_source_id == ds.id).order_by(desc(IngestionRun.started_at)).limit(1))
        last_ok = s.scalar(select(func.max(IngestionRun.finished_at)).where(
            IngestionRun.data_source_id == ds.id, IngestionRun.status.in_(["success", "partial"])))
        batches = dict(s.execute(select(IngestionBatch.status, func.count()).where(IngestionBatch.data_source_id == ds.id)
                                 .group_by(IngestionBatch.status)).all())
        failed_24h = s.scalar(select(func.count()).where(IngestionRun.data_source_id == ds.id, IngestionRun.status == "failed",
                                                         IngestionRun.started_at >= utcnow() - timedelta(hours=24)))
        stale = None
        if ds.enabled and ds.expected_interval_minutes:
            stale = last_ok is None or (utcnow() - last_ok) > timedelta(minutes=3 * ds.expected_interval_minutes)
        sources.append({
            "key": ds.key, "name": ds.name, "module": ds.module, "kind": ds.kind, "enabled": ds.enabled,
            "license": ds.license, "citation": ds.citation, "expected_interval_minutes": ds.expected_interval_minutes,
            "last_run": {"status": last.status, "started_at": iso(last.started_at), "finished_at": iso(last.finished_at),
                         "records_fetched": last.records_fetched, "records_inserted": last.records_inserted,
                         "error": last.error} if last else None,
            "last_success_at": iso(last_ok), "stale": stale, "batches": batches, "failed_runs_24h": failed_24h,
            "permissions": {k: getattr(ds.permission, k) for k in ("allow_ai_processing", "allow_ml_training", "allow_export",
                                                                   "allow_redistribution")} if ds.permission else None,
        })
    delayed = s.execute(select(IngestionBatch.batch_key, IngestionBatch.status, IngestionBatch.attempts, IngestionBatch.last_error)
                        .where(IngestionBatch.status.in_(["pending", "failed", "missing_upstream"]))
                        .order_by(desc(IngestionBatch.batch_time)).limit(25)).all()
    workers = []
    try:
        from app.workers.locks import redis_client

        now = time.time()
        for host, ts in (redis_client().hgetall("georisk:workers") or {}).items():
            age = now - float(ts)
            workers.append({"host": host, "last_heartbeat_seconds_ago": round(age, 1), "healthy": age < 180})
        redis_ok = True
    except Exception:  # noqa: BLE001 - status must render even if Redis is down
        redis_ok = False
    since = utcnow() - timedelta(hours=24)
    coverage = {
        "events_24h": s.scalar(select(func.count()).where(NormalizedGeopoliticalEvent.available_at >= since)),
        "countries_with_events_24h": s.scalar(text(
            "SELECT count(DISTINCT action_country_id) FROM normalized_geopolitical_events WHERE available_at >= :s"), {"s": since}),
        "total_events": s.scalar(text("SELECT reltuples::bigint FROM pg_class WHERE relname='normalized_geopolitical_events'")),
        "earliest_event_available_at": iso(s.scalar(select(func.min(NormalizedGeopoliticalEvent.available_at)))),
        "latest_event_available_at": iso(s.scalar(select(func.max(NormalizedGeopoliticalEvent.available_at)))),
    }
    return {"generated_at": iso(utcnow()), "sources": sources, "delayed_or_failed_batches": [
        {"batch": b, "status": st, "attempts": at, "error": err} for b, st, at, err in delayed],
        "workers": workers, "redis_ok": redis_ok, "coverage": coverage}
