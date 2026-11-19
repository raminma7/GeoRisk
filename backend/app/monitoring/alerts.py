"""Evidence-based alert generation.

Alerts are created when configured thresholds are crossed (event-driven, right
after each priority update). Every alert embeds its supporting evidence:
priority components, supporting event clusters with source links, and the
number of independent sources. Major alerts require human review.
"""

from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.orm import Session

from app.core.db import utcnow
from app.core.logging import get_logger
from app.core.runtime_settings import get_value
from app.crises.models import Crisis
from app.geopolitics.models import EventCluster, NormalizedGeopoliticalEvent
from app.monitoring.models import GeopoliticalAlert, MonitoringPriority
from app.monitoring.priority import LEVEL_ORDER
from app.notifications.dispatch import dispatch_alert

log = get_logger(__name__)


def _previous(session: Session, mp: MonitoringPriority) -> MonitoringPriority | None:
    return session.scalar(
        select(MonitoringPriority)
        .where(MonitoringPriority.crisis_id == mp.crisis_id, MonitoringPriority.id < mp.id)
        .order_by(MonitoringPriority.id.desc())
        .limit(1)
    )


def evidence_bundle(session: Session, mp: MonitoringPriority) -> dict:
    clusters = []
    if mp.supporting_event_cluster_ids:
        rows = session.execute(
            select(EventCluster, NormalizedGeopoliticalEvent.source_url)
            .outerjoin(NormalizedGeopoliticalEvent, NormalizedGeopoliticalEvent.id == EventCluster.representative_event_id)
            .where(EventCluster.id.in_(mp.supporting_event_cluster_ids))
        ).all()
        for cl, url in rows:
            clusters.append({
                "event_cluster_id": cl.id, "title": cl.title, "event_date": str(cl.event_date),
                "verification_status": cl.verification_status, "independent_sources": len(cl.source_domains or []),
                "reports": cl.member_count, "example_source_url": url,
            })
    return {
        "priority_id": mp.id, "score": mp.score, "level": mp.level, "direction": mp.direction,
        "algorithm_version": mp.algorithm_version, "computed_at": mp.computed_at.isoformat(),
        "triggering_indicators": mp.triggering_indicators, "independent_source_count": mp.independent_source_count,
        "evidence_confidence": mp.evidence_confidence, "evidence_confidence_label": mp.evidence_confidence_label,
        "supporting_event_clusters": clusters, "explanation": mp.explanation,
        "caveat": "Based on automatically extracted news events (GDELT); not independently verified.",
    }


def _create(session: Session, *, dedup_key: str, crisis: Crisis, mp: MonitoringPriority | None, alert_type: str,
            severity: str, title: str, message: str, evidence: dict, requires_review: bool) -> GeopoliticalAlert | None:
    stmt = insert(GeopoliticalAlert).values(
        dedup_key=dedup_key, crisis_id=crisis.id, priority_id=mp.id if mp else None, alert_type=alert_type,
        severity=severity, title=title[:300], message=message, evidence=evidence, status="open",
        requires_review=requires_review, notified=False, created_at=utcnow(),
    ).on_conflict_do_nothing(index_elements=["dedup_key"]).returning(GeopoliticalAlert.id)
    aid = session.execute(stmt).scalar()
    if aid is None:
        return None
    alert = session.get(GeopoliticalAlert, aid)
    log.info("alert_created", alert_id=aid, type=alert_type, crisis=crisis.uid, severity=severity)
    return alert


def evaluate_alerts(session: Session, priorities: list[MonitoringPriority]) -> list[GeopoliticalAlert]:
    threshold_level = get_value(session, "alert_threshold_level")
    thr = LEVEL_ORDER.index(threshold_level) if threshold_level in LEVEL_ORDER else 2
    created: list[GeopoliticalAlert] = []
    day = utcnow().strftime("%Y%m%d")
    for mp in priorities:
        crisis = session.get(Crisis, mp.crisis_id)
        if crisis is None:
            continue
        prev = _previous(session, mp)
        rank = LEVEL_ORDER.index(mp.level)
        prev_rank = LEVEL_ORDER.index(prev.level) if prev else -1
        evidence = None

        if prev is None:  # first scoring of a new crisis
            evidence = evidence_bundle(session, mp)
            a = _create(session, dedup_key=f"new_crisis:{crisis.id}", crisis=crisis, mp=mp, alert_type="new_crisis",
                        severity="info" if rank < thr else mp.level,
                        title=f"New monitored situation: {crisis.title}",
                        message=f"Automatic crisis discovery created {crisis.uid}. {mp.explanation}",
                        evidence=evidence, requires_review=True)
            if a:
                created.append(a)

        if rank >= thr and prev_rank < rank:
            evidence = evidence or evidence_bundle(session, mp)
            a = _create(session, dedup_key=f"priority:{crisis.id}:{mp.level}:{day}", crisis=crisis, mp=mp,
                        alert_type="priority_threshold", severity=mp.level,
                        title=f"Review priority {mp.level}: {crisis.title}",
                        message=mp.explanation, evidence=evidence, requires_review=mp.level == "critical")
            if a:
                created.append(a)

        if mp.direction == "escalating" and (prev is None or prev.direction != "escalating") and rank >= 1:
            evidence = evidence or evidence_bundle(session, mp)
            a = _create(session, dedup_key=f"escalation:{crisis.id}:{day}", crisis=crisis, mp=mp,
                        alert_type="escalation", severity=mp.level,
                        title=f"Escalation indicators: {crisis.title}",
                        message="Conflict-coded event intensity in the last 24h rose markedly versus the prior 72h "
                                f"daily average. {mp.explanation}",
                        evidence=evidence, requires_review=rank >= 2)
            if a:
                created.append(a)

        if mp.direction == "de_escalating" and (prev is None or prev.direction != "de_escalating"):
            evidence = evidence or evidence_bundle(session, mp)
            a = _create(session, dedup_key=f"de_escalation:{crisis.id}:{day}", crisis=crisis, mp=mp,
                        alert_type="de_escalation", severity="info",
                        title=f"De-escalation indicators: {crisis.title}",
                        message="Conflict-coded intensity fell versus the prior 72h while cooperation or "
                                f"de-escalation-coded events were observed. {mp.explanation}",
                        evidence=evidence, requires_review=False)
            if a:
                created.append(a)
    session.flush()
    for a in created:
        dispatch_alert(session, a)
    return created
