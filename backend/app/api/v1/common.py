"""Shared API helpers: pagination, filters, serializers."""

from __future__ import annotations

from datetime import date, datetime, timedelta
from typing import Any, Generic, TypeVar

from fastapi import HTTPException, Query
from pydantic import BaseModel
from sqlalchemy import func, or_, select
from sqlalchemy.orm import Session

from app.core.db import utcnow
from app.crises.models import Crisis
from app.geopolitics import cameo
from app.geopolitics.models import EventCluster, NewsArticle, NormalizedGeopoliticalEvent
from app.registry.models import Country, Region

T = TypeVar("T")


class Page(BaseModel, Generic[T]):
    total: int
    limit: int
    offset: int
    items: list[T]


class Pagination:
    def __init__(self, limit: int = Query(50, ge=1, le=500), offset: int = Query(0, ge=0)):
        self.limit = limit
        self.offset = offset


def paginate(session: Session, stmt, p: Pagination, serialize) -> dict:
    total = session.scalar(select(func.count()).select_from(stmt.order_by(None).subquery())) or 0
    rows = session.execute(stmt.limit(p.limit).offset(p.offset)).all()
    return {"total": total, "limit": p.limit, "offset": p.offset,
            "items": [serialize(r[0] if len(r) == 1 else r) for r in rows]}


def parse_countries(session: Session, codes: list[str]) -> list[Country]:
    out = []
    for code in codes:
        c = get_country(session, code)
        out.append(c)
    return out


def get_country(session: Session, code: str) -> Country:
    code = code.strip().upper()
    q = select(Country).where(Country.iso3 == code) if len(code) == 3 else select(Country).where(Country.iso2 == code)
    c = session.scalar(q)
    if c is None:
        raise HTTPException(404, f"Unknown country code '{code}' (use ISO 3166 alpha-3 or alpha-2)")
    return c


def get_crisis(session: Session, ident: str) -> Crisis:
    c = session.scalar(select(Crisis).where(Crisis.uid == ident)) if not ident.isdigit() else session.get(Crisis, int(ident))
    if c is None:
        raise HTTPException(404, "Crisis not found")
    if c.status == "merged" and (c.meta or {}).get("merged_into"):
        target = session.get(Crisis, c.meta["merged_into"])
        if target:
            return target
    return c


def region_iso3s(session: Session, region_code: str) -> list[str]:
    r = session.scalar(select(Region).where(Region.code == region_code))
    if r is None:
        raise HTTPException(404, f"Unknown region '{region_code}'")
    return list(session.scalars(select(Country.iso3).where(
        or_(Country.subregion_id == r.id, Country.continent_id == r.id))).all())


def default_range(start: date | None, end: date | None, days: int = 30) -> tuple[datetime, datetime]:
    e = datetime.combine(end, datetime.max.time()).replace(tzinfo=utcnow().tzinfo) if end else utcnow()
    s = datetime.combine(start, datetime.min.time()).replace(tzinfo=utcnow().tzinfo) if start else e - timedelta(days=days)
    return s, e


def iso(dt: datetime | date | None) -> str | None:
    return dt.isoformat() if dt else None


def ser_country(c: Country, region_names: dict[int, str] | None = None) -> dict:
    rn = region_names or {}
    return {"iso3": c.iso3, "iso2": c.iso2, "iso_numeric": c.iso_numeric, "name": c.name, "official_name": c.official_name,
            "region": rn.get(c.subregion_id) if c.subregion_id else None,
            "continent": rn.get(c.continent_id) if c.continent_id else None, "is_historical": c.is_historical}


def ser_cluster(cl: EventCluster, example_url: str | None = None) -> dict:
    return {
        "id": cl.id, "kind": cl.kind, "title": cl.title, "event_date": iso(cl.event_date),
        "category": cameo.category(cl.event_root_code), "category_label": cameo.category_label(cl.event_root_code),
        "quad_class": cameo.QUAD_CLASSES.get(cl.quad_class or 0), "countries": cl.country_iso3s,
        "location_country": cl.action_country_iso3, "first_seen_at": iso(cl.first_seen_at),
        "last_seen_at": iso(cl.last_seen_at), "reports": cl.member_count,
        "independent_sources": len(cl.source_domains or []), "source_domains": (cl.source_domains or [])[:15],
        "max_severity": round(cl.max_severity or 0, 3), "latitude": cl.latitude, "longitude": cl.longitude,
        "verification_status": cl.verification_status, "example_source_url": example_url,
        "evidence_type": "source_reported_machine_extracted",
    }


def ser_event(e: NormalizedGeopoliticalEvent, iso_by_id: dict[int, str]) -> dict:
    return {
        "id": e.id, "source": "gdelt_events", "source_record_id": e.source_record_id,
        "event_date": iso(e.event_date), "available_at": iso(e.available_at), "event_code": e.event_code,
        "category": e.category, "category_label": cameo.category_label(e.event_root_code),
        "quad_class": cameo.QUAD_CLASSES.get(e.quad_class or 0), "goldstein": e.goldstein,
        "severity": e.severity, "signal_type": e.signal_type,
        "actor1": {"name": e.actor1_name, "country": iso_by_id.get(e.actor1_country_id)},
        "actor2": {"name": e.actor2_name, "country": iso_by_id.get(e.actor2_country_id)},
        "location": {"name": e.action_location, "country": iso_by_id.get(e.action_country_id),
                     "latitude": e.latitude, "longitude": e.longitude},
        "num_sources": e.num_sources, "num_articles": e.num_articles, "num_mentions": e.num_mentions,
        "source_url": e.source_url, "source_domain": e.source_domain, "cluster_id": e.cluster_id,
        "verification_status": e.verification_status, "evidence_type": "source_reported_machine_extracted",
    }


def ser_article(a: NewsArticle) -> dict:
    return {"id": a.id, "title": a.title, "url": a.url, "domain": a.domain, "language": a.language,
            "source_country": a.source_country_name, "seen_at": iso(a.seen_at), "topic": a.topic,
            "countries": a.country_iso3s, "cluster_id": a.cluster_id, "queries": a.query_keys,
            "evidence_type": "source_reported"}


def ser_crisis_card(c: Crisis, region: str | None, names: dict[str, str]) -> dict:
    ind = c.current_indicators or {}
    return {
        "id": c.id, "uid": c.uid, "title": c.title, "status": c.status, "crisis_type": c.crisis_type,
        "region": region, "countries": [{"iso3": x, "name": names.get(x, x)}
                                        for x in sorted(set(c.core_country_iso3s or []) | set(c.location_country_iso3s or []))],
        "location_countries": c.location_country_iso3s, "first_observed_at": iso(c.first_observed_at),
        "last_event_at": iso(c.last_event_at), "last_updated_at": iso(c.last_updated_at),
        "priority": {"score": c.current_priority, "level": c.priority_level, "direction": c.direction,
                     "note": "Review priority, not a probability of conflict."},
        "indicator_change": {"clusters_24h": ind.get("clusters_24h"), "clusters_prev_24h": ind.get("clusters_prev_24h"),
                             "material_conflict_24h": ind.get("material_conflict_24h"),
                             "diplomatic_signals_24h": ind.get("diplomatic_signals_24h"), "change": ind.get("change")},
        "evidence_confidence": {"score": ind.get("evidence_confidence"), "label": ind.get("evidence_confidence_label")},
        "independent_sources_24h": ind.get("independent_sources_24h"),
        "verification_status": c.verification_status, "needs_review": c.needs_review,
        "latitude": c.latitude, "longitude": c.longitude, "event_cluster_count": c.event_cluster_count,
        "title_source": c.title_source, "algorithm_version": c.algorithm_version,
    }


def country_names(session: Session) -> dict[str, str]:
    return dict(session.execute(select(Country.iso3, Country.name)).all())


def region_names(session: Session) -> dict[int, str]:
    return dict(session.execute(select(Region.id, Region.name)).all())


def jsonable(v: Any) -> Any:
    if isinstance(v, (datetime, date)):
        return v.isoformat()
    return v
