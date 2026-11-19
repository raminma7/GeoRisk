"""Crises: listing, detail, timeline, evidence, review."""

from __future__ import annotations

from typing import Literal

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, Field
from sqlalchemy import desc, func, or_, select, text
from sqlalchemy.orm import Session

from app.ai.models import GeopoliticalAnalysis
from app.api.v1.common import (
    Pagination,
    country_names,
    get_crisis,
    iso,
    paginate,
    region_names,
    ser_article,
    ser_cluster,
    ser_crisis_card,
)
from app.core.db import get_db, utcnow
from app.core.security import Principal, require_read, require_role
from app.crises.models import Crisis, CrisisArticle, CrisisEvent, CrisisEvidenceLog, CrisisParticipant, CrisisSnapshot
from app.geopolitics.models import VERIFICATION_MULTI_SOURCE, EventCluster, NewsArticle, NormalizedGeopoliticalEvent
from app.ingestion.models import AuditLog
from app.monitoring.models import MonitoringPriority
from app.registry.models import Actor, Country, Region

router = APIRouter(prefix="/crises", tags=["Crises"])


@router.get("", summary="Monitored crises (multi-country, multi-actor)")
def list_crises(status: str | None = Query(None, description="candidate|active|dormant|resolved|rejected"),
                region: str | None = None, country: str | None = None, min_priority: float = 0.0,
                needs_review: bool | None = None, sort: Literal["priority", "recent", "first_observed"] = "priority",
                p: Pagination = Depends(), s: Session = Depends(get_db), _: Principal = Depends(require_read)):
    q = select(Crisis).where(Crisis.current_priority >= min_priority)
    q = q.where(Crisis.status == status) if status else q.where(Crisis.status.in_(["candidate", "active", "dormant"]))
    if country:
        c = country.upper()
        q = q.where(or_(Crisis.core_country_iso3s.contains([c]), Crisis.location_country_iso3s.contains([c])))
    if region:
        r = s.scalar(select(Region).where(Region.code == region))
        if r:
            q = q.where(Crisis.region_id == r.id)
    if needs_review is not None:
        q = q.where(Crisis.needs_review.is_(needs_review))
    order = {"priority": desc(Crisis.current_priority), "recent": desc(Crisis.last_event_at),
             "first_observed": desc(Crisis.first_observed_at)}[sort]
    names, regions = country_names(s), region_names(s)
    return paginate(s, q.order_by(order), p, lambda c: ser_crisis_card(c, regions.get(c.region_id), names))


def _spillover(s: Session, crisis_id: str) -> list[dict]:
    from app.api.v1.predictions import crisis_spillover

    try:
        return crisis_spillover(s, crisis_id)
    except Exception:  # noqa: BLE001 - optional enrichment must never break the crisis page
        return []


@router.get("/{crisis_id}", summary="Crisis detail with participants, priority explanation and evidence trail")
def crisis_detail(crisis_id: str, s: Session = Depends(get_db), _: Principal = Depends(require_read)):
    c = get_crisis(s, crisis_id)
    names, regions = country_names(s), region_names(s)
    parts = s.execute(select(CrisisParticipant, Country.iso3, Country.name, Actor.name, Actor.actor_type)
                      .outerjoin(Country, Country.id == CrisisParticipant.country_id)
                      .outerjoin(Actor, Actor.id == CrisisParticipant.actor_id)
                      .where(CrisisParticipant.crisis_id == c.id)
                      .order_by(desc(CrisisParticipant.event_count))).all()
    mp = s.scalar(select(MonitoringPriority).where(MonitoringPriority.crisis_id == c.id).order_by(desc(MonitoringPriority.id)).limit(1))
    log = s.scalars(select(CrisisEvidenceLog).where(CrisisEvidenceLog.crisis_id == c.id)
                    .order_by(desc(CrisisEvidenceLog.created_at)).limit(50))
    arts = s.scalars(select(NewsArticle).join(CrisisArticle, CrisisArticle.article_id == NewsArticle.id)
                     .where(CrisisArticle.crisis_id == c.id).order_by(desc(NewsArticle.seen_at)).limit(30))
    from app.api.v1.monitoring import ser_priority

    return {
        **ser_crisis_card(c, regions.get(c.region_id), names),
        "summary": c.summary,
        "participants": [{"key": p.participant_key, "type": "country" if p.country_id else "actor",
                          "iso3": ciso, "name": cname or aname, "actor_type": atype, "role": p.role,
                          "event_clusters": p.event_count, "first_seen_at": iso(p.first_seen_at),
                          "last_seen_at": iso(p.last_seen_at), "active": p.active}
                         for p, ciso, cname, aname, atype in parts],
        "priority_detail": ser_priority(mp) if mp else None,
        "evidence_log": [{"action": e.action, "reason": e.reason, "evidence": e.evidence, "created_at": iso(e.created_at),
                          "algorithm_version": e.algorithm_version} for e in log],
        "related_articles": [ser_article(a) for a in arts],
        "review": {"needs_review": c.needs_review, "note": c.review_note, "reviewed_by": c.reviewed_by},
        "spillover_risk": _spillover(s, str(c.id)),
        "meta": c.meta,
    }


@router.get("/{crisis_id}/timeline", summary="Chronological evidence with verified/unverified separation")
def crisis_timeline(crisis_id: str, verification: str | None = None, p: Pagination = Depends(),
                    s: Session = Depends(get_db), _: Principal = Depends(require_read)):
    c = get_crisis(s, crisis_id)
    q = (select(EventCluster, NormalizedGeopoliticalEvent.source_url, CrisisEvent.match_score, CrisisEvent.needs_review)
         .join(CrisisEvent, CrisisEvent.event_cluster_id == EventCluster.id)
         .outerjoin(NormalizedGeopoliticalEvent, NormalizedGeopoliticalEvent.id == EventCluster.representative_event_id)
         .where(CrisisEvent.crisis_id == c.id))
    if verification:
        q = q.where(EventCluster.verification_status == verification)
    page = paginate(s, q.order_by(desc(EventCluster.last_seen_at)), p,
                    lambda r: {**ser_cluster(r[0], r[1]), "match_score": r[2], "match_needs_review": r[3]})
    daily = s.execute(text(
        "SELECT ec.event_date, count(*), count(*) FILTER (WHERE ec.quad_class=4), count(*) FILTER (WHERE ec.quad_class=3), "
        " count(*) FILTER (WHERE ec.quad_class IN (1,2)), count(*) FILTER (WHERE ec.verification_status=:ms) "
        "FROM event_clusters ec JOIN crisis_events ce ON ce.event_cluster_id = ec.id WHERE ce.crisis_id = :cid "
        "GROUP BY 1 ORDER BY 1"), {"cid": c.id, "ms": VERIFICATION_MULTI_SOURCE}).all()
    snaps = s.scalars(select(CrisisSnapshot).where(CrisisSnapshot.crisis_id == c.id).order_by(CrisisSnapshot.as_of).limit(2000))
    counts = dict(s.execute(select(EventCluster.verification_status, func.count())
                            .join(CrisisEvent, CrisisEvent.event_cluster_id == EventCluster.id)
                            .where(CrisisEvent.crisis_id == c.id).group_by(EventCluster.verification_status)).all())
    return {
        "crisis_id": c.id, "uid": c.uid, "events": page,
        "verification_breakdown": counts,
        "verification_legend": {
            "machine_extracted": "Single automated extraction from news; unverified.",
            "multi_source_reported": "Reported by ≥3 independent outlets; still unverified.",
            "curated_dataset": "Human-coded research dataset (e.g. UCDP).",
        },
        "daily": [{"date": d.isoformat(), "clusters": n, "material_conflict": mc, "verbal_conflict": vc,
                   "cooperation": co, "multi_source": ms} for d, n, mc, vc, co, ms in daily],
        "priority_history": [{"as_of": iso(x.as_of), "priority": x.priority, "level": x.priority_level,
                              "direction": x.direction} for x in snaps],
    }


@router.get("/{crisis_id}/analysis", summary="Latest AI crisis analysis (AI interpretation, not ground truth)")
def crisis_analysis(crisis_id: str, s: Session = Depends(get_db), _: Principal = Depends(require_read)):
    from app.api.v1.analysis import analysis_response

    c = get_crisis(s, crisis_id)
    a = s.scalar(select(GeopoliticalAnalysis).where(GeopoliticalAnalysis.scope_type == "crisis",
                                                    GeopoliticalAnalysis.scope_key == str(c.id))
                 .order_by(desc(GeopoliticalAnalysis.created_at)).limit(1))
    return analysis_response(a)


class CrisisReview(BaseModel):
    action: Literal["confirm", "reject", "resolve", "set_title", "clear_review"]
    title: str | None = Field(None, max_length=300)
    note: str | None = Field(None, max_length=4000)


@router.post("/{crisis_id}/review", summary="Human review of ambiguous clustering / crisis status (analyst)")
def review_crisis(crisis_id: str, body: CrisisReview, s: Session = Depends(get_db),
                  p: Principal = Depends(require_role("analyst"))):
    c = get_crisis(s, crisis_id)
    if body.action == "confirm":
        c.status, c.needs_review = "active", False
    elif body.action == "reject":
        c.status, c.needs_review = "rejected", False
    elif body.action == "resolve":
        c.status = "resolved"
    elif body.action == "set_title":
        if not body.title:
            raise HTTPException(422, "title required")
        c.title, c.title_source = body.title, "human"
    elif body.action == "clear_review":
        c.needs_review = False
        s.execute(text("UPDATE crisis_events SET needs_review = false WHERE crisis_id = :c"), {"c": c.id})
    c.review_note = body.note or c.review_note
    c.reviewed_by = p.name
    c.last_updated_at = utcnow()
    s.add(CrisisEvidenceLog(crisis_id=c.id, action="reviewed", reason=f"Human review: {body.action}",
                            evidence=body.model_dump(), algorithm_version="human"))
    s.add(AuditLog(actor=p.name, action="crisis_review", target=f"crisis:{c.id}", details=body.model_dump()))
    s.commit()
    return {"id": c.id, "status": c.status, "needs_review": c.needs_review, "title": c.title}
