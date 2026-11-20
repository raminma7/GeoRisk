"""Global News & Events explorer."""

from __future__ import annotations

from datetime import date

from fastapi import APIRouter, Depends, Query
from sqlalchemy import desc, select
from sqlalchemy.orm import Session

from app.api.v1.common import (
    Pagination,
    default_range,
    get_crisis,
    paginate,
    region_iso3s,
    ser_article,
    ser_cluster,
    ser_event,
)
from app.core.db import get_db
from app.core.security import Principal, require_read
from app.crises.models import CrisisArticle, CrisisEvent
from app.geopolitics import cameo
from app.geopolitics.models import EventCluster, NewsArticle, NormalizedGeopoliticalEvent
from app.registry.models import Country

router = APIRouter(tags=["News and Events"])


@router.get("/news", summary="Search discovered news (metadata + links only)")
def news(q: str | None = Query(None, max_length=200), country: str | None = None, region: str | None = None,
         topic: str | None = None, source: str | None = Query(None, description="domain"),
         source_country: str | None = None, language: str | None = None, crisis: str | None = None,
         start: date | None = None, end: date | None = None,
         clustered: bool = Query(False, description="return deduplicated news clusters instead of articles"),
         p: Pagination = Depends(), s: Session = Depends(get_db), _: Principal = Depends(require_read)):
    st, en = default_range(start, end, days=7)
    stmt = select(NewsArticle).where(NewsArticle.seen_at.between(st, en))
    if q:
        stmt = stmt.where(NewsArticle.title.ilike(f"%{q}%"))
    if country:
        stmt = stmt.where(NewsArticle.country_iso3s.contains([country.upper()]))
    if region:
        stmt = stmt.where(NewsArticle.country_iso3s.overlap(region_iso3s(s, region)))
    if topic:
        stmt = stmt.where(NewsArticle.topic == topic)
    if source:
        stmt = stmt.where(NewsArticle.domain == source.lower())
    if source_country:
        stmt = stmt.where(NewsArticle.source_country_name.ilike(source_country))
    if language:
        stmt = stmt.where(NewsArticle.language.ilike(language))
    if crisis:
        c = get_crisis(s, crisis)
        stmt = stmt.where(NewsArticle.id.in_(select(CrisisArticle.article_id).where(CrisisArticle.crisis_id == c.id)))
    if clustered:
        cl = (select(EventCluster).where(EventCluster.id.in_(stmt.with_only_columns(NewsArticle.cluster_id)))
              .order_by(desc(EventCluster.member_count), desc(EventCluster.last_seen_at)))

        def ser(c: EventCluster) -> dict:
            arts = s.scalars(select(NewsArticle).where(NewsArticle.cluster_id == c.id).order_by(NewsArticle.seen_at).limit(10))
            return {**ser_cluster(c), "articles": [ser_article(a) for a in arts]}

        return paginate(s, cl, p, ser)
    return paginate(s, stmt.order_by(desc(NewsArticle.seen_at)), p, ser_article)


@router.get("/events", summary="Normalized geopolitical events (or deduplicated clusters / map points)")
def events(country: str | None = None, region: str | None = None, category: str | None = None,
           root_code: str | None = None, quad_class: int | None = Query(None, ge=1, le=4),
           verification: str | None = None, crisis: str | None = None, start: date | None = None,
           end: date | None = None, min_severity: float = 0.0,
           located_in: str | None = Query(None, description="ISO3: only events that happened in this country (map/clusters)"),
           view: str = Query("events", pattern="^(events|clusters|map)$"),
           p: Pagination = Depends(), s: Session = Depends(get_db), _: Principal = Depends(require_read)):
    st, en = default_range(start, end, days=2)
    if view in ("clusters", "map"):
        q = select(EventCluster).where(EventCluster.kind == "event", EventCluster.last_seen_at.between(st, en),
                                       EventCluster.max_severity >= min_severity)
        if country:
            q = q.where(EventCluster.country_iso3s.contains([country.upper()]))
        if located_in:
            q = q.where(EventCluster.action_country_iso3 == located_in.upper())
        if region:
            q = q.where(EventCluster.action_country_iso3.in_(region_iso3s(s, region)))
        if root_code:
            q = q.where(EventCluster.event_root_code == root_code)
        if category:
            root = next((k for k, v in cameo.ROOT_CATEGORIES.items() if v[0] == category), None)
            q = q.where(EventCluster.event_root_code == root)
        if quad_class:
            q = q.where(EventCluster.quad_class == quad_class)
        if verification:
            q = q.where(EventCluster.verification_status == verification)
        if crisis:
            c = get_crisis(s, crisis)
            q = q.where(EventCluster.id.in_(select(CrisisEvent.event_cluster_id).where(CrisisEvent.crisis_id == c.id)))
        if view == "map":
            q = q.where(EventCluster.latitude.is_not(None)).order_by(desc(EventCluster.max_severity),
                                                                    desc(EventCluster.member_count))
            rows = s.scalars(q.limit(min(p.limit * 4, 2000))).all()
            from app.geopolitics.models import NormalizedGeopoliticalEvent as NGE
            from app.monitoring import plain

            reps = {e.id: e for e in s.scalars(select(NGE).where(NGE.id.in_(
                [c.representative_event_id for c in rows if c.representative_event_id])))}
            texts = plain.incident_texts(s, [(c, reps.get(c.representative_event_id)) for c in rows])
            return {"type": "FeatureCollection", "features": [
                {"type": "Feature", "geometry": {"type": "Point", "coordinates": [c.longitude, c.latitude]},
                 "properties": {"id": c.id, "title": c.title, "text": texts.get(c.id) or c.title, "severity": c.max_severity,
                                "category": cameo.category(c.event_root_code), "quad_class": c.quad_class,
                                "verification_status": c.verification_status, "reports": c.member_count,
                                "sources": len(c.source_domains or []), "date": c.event_date.isoformat() if c.event_date else None}}
                for c in rows]}
        return paginate(s, q.order_by(desc(EventCluster.last_seen_at)), p, ser_cluster)

    q = select(NormalizedGeopoliticalEvent).where(NormalizedGeopoliticalEvent.available_at.between(st, en),
                                                  NormalizedGeopoliticalEvent.severity >= min_severity)
    if country:
        q = q.where(NormalizedGeopoliticalEvent.country_iso3s.contains([country.upper()]))
    if region:
        q = q.where(NormalizedGeopoliticalEvent.country_iso3s.overlap(region_iso3s(s, region)))
    if category:
        q = q.where(NormalizedGeopoliticalEvent.category == category)
    if root_code:
        q = q.where(NormalizedGeopoliticalEvent.event_root_code == root_code)
    if quad_class:
        q = q.where(NormalizedGeopoliticalEvent.quad_class == quad_class)
    if verification:
        q = q.where(NormalizedGeopoliticalEvent.verification_status == verification)
    if crisis:
        c = get_crisis(s, crisis)
        q = q.where(NormalizedGeopoliticalEvent.cluster_id.in_(
            select(CrisisEvent.event_cluster_id).where(CrisisEvent.crisis_id == c.id)))
    iso_by_id = dict(s.execute(select(Country.id, Country.iso3)).all())
    return paginate(s, q.order_by(desc(NormalizedGeopoliticalEvent.available_at)), p, lambda e: ser_event(e, iso_by_id))


@router.get("/events/categories", summary="CAMEO event categories")
def categories(_: Principal = Depends(require_read)):
    return {"items": [{"root_code": k, "category": v[0], "label": v[1]} for k, v in cameo.ROOT_CATEGORIES.items()],
            "quad_classes": cameo.QUAD_CLASSES}
