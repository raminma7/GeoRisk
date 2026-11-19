"""Countries, regions and multi-country intelligence."""

from __future__ import annotations

from datetime import date, timedelta
from itertools import combinations

from fastapi import APIRouter, Depends, Query
from pydantic import BaseModel, Field
from sqlalchemy import desc, func, or_, select, text
from sqlalchemy.orm import Session

from app.ai.models import GeopoliticalAnalysis
from app.api.v1.common import (
    Pagination,
    country_names,
    default_range,
    get_country,
    iso,
    paginate,
    parse_countries,
    region_names,
    ser_country,
    ser_crisis_card,
    ser_event,
)
from app.core.db import get_db, utcnow
from app.core.security import Principal, require_read
from app.crises.models import Crisis
from app.geopolitics.models import (
    CountryRelationship,
    EventCountryRole,
    GeopoliticalIndicator,
    NormalizedGeopoliticalEvent,
)
from app.registry.models import Country, CountryAlias, CountryGroup, CountryGroupMember, Region

router = APIRouter(tags=["Countries"])
MAX_COMPARE = 25  # practical query/performance bound for multi-country analysis


@router.get("/regions")
def list_regions(kind: str | None = None, s: Session = Depends(get_db), _: Principal = Depends(require_read)):
    q = select(Region).order_by(Region.kind, Region.name)
    if kind:
        q = q.where(Region.kind == kind)
    return {"items": [{"code": r.code, "name": r.name, "kind": r.kind} for r in s.scalars(q)]}


@router.get("/countries", summary="Searchable country registry")
def list_countries(q: str | None = Query(None, description="name, alias or code"), region: str | None = None,
                   include_historical: bool = False, p: Pagination = Depends(), s: Session = Depends(get_db),
                   _: Principal = Depends(require_read)):
    stmt = select(Country)
    if not include_historical:
        stmt = stmt.where(Country.is_historical.is_(False))
    if q:
        like = f"%{q.lower()}%"
        alias_ids = select(CountryAlias.country_id).where(CountryAlias.alias_norm.like(like))
        stmt = stmt.where(or_(func.lower(Country.name).like(like), Country.iso3 == q.upper(), Country.iso2 == q.upper(),
                              Country.id.in_(alias_ids)))
    if region:
        r = s.scalar(select(Region).where(Region.code == region))
        if r:
            stmt = stmt.where(or_(Country.subregion_id == r.id, Country.continent_id == r.id))
    rn = region_names(s)
    return paginate(s, stmt.order_by(Country.name), p, lambda c: ser_country(c, rn))


def _latest_indicators(s: Session, iso3: str) -> dict:
    as_of = s.scalar(select(func.max(GeopoliticalIndicator.as_of)).where(
        GeopoliticalIndicator.scope_type == "country", GeopoliticalIndicator.scope_key == iso3))
    if not as_of:
        return {}
    rows = s.scalars(select(GeopoliticalIndicator).where(
        GeopoliticalIndicator.scope_type == "country", GeopoliticalIndicator.scope_key == iso3,
        GeopoliticalIndicator.as_of == as_of))
    return {"as_of": iso(as_of), "window": "24h", "values": {
        r.indicator: {"value": r.value, "baseline_mean": r.baseline_mean, "zscore": r.zscore} for r in rows}}


def _country_crises(s: Session, iso3: str) -> list[dict]:
    names, regions = country_names(s), region_names(s)
    q = select(Crisis).where(Crisis.status.in_(["candidate", "active", "dormant"]),
                             or_(Crisis.core_country_iso3s.contains([iso3]), Crisis.location_country_iso3s.contains([iso3]))
                             ).order_by(desc(Crisis.current_priority))
    return [ser_crisis_card(c, regions.get(c.region_id), names) for c in s.scalars(q)]


def _relationships(s: Session, cid: int, limit: int = 15) -> list[dict]:
    latest = s.scalar(select(func.max(CountryRelationship.window_end)))
    if not latest:
        return []
    rows = s.execute(text(
        "SELECT CASE WHEN source_country_id = :c THEN target_country_id ELSE source_country_id END AS other, "
        " sum(event_clusters), sum(material_conflict), sum(verbal_conflict), sum(verbal_cooperation), "
        " sum(material_cooperation), avg(avg_goldstein) "
        "FROM country_relationships WHERE (source_country_id = :c OR target_country_id = :c) AND window_end = :w "
        "GROUP BY 1 ORDER BY 2 DESC LIMIT :lim"), {"c": cid, "w": latest, "lim": limit}).all()
    iso_by_id = dict(s.execute(select(Country.id, Country.iso3)).all())
    names = country_names(s)
    return [{"country": iso_by_id.get(o), "name": names.get(iso_by_id.get(o)), "interaction_clusters_7d": int(n),
             "material_conflict": int(mc), "verbal_conflict": int(vc), "verbal_cooperation": int(vco),
             "material_cooperation": int(mco), "avg_goldstein": round(g, 2) if g is not None else None}
            for o, n, mc, vc, vco, mco, g in rows]


def daily_series(s: Session, iso3s: list[str], days: int = 90) -> dict[str, list[dict]]:
    rows = s.execute(text(
        "SELECT iso, event_date, count(*) FILTER (WHERE quad_class=4), count(*) FILTER (WHERE quad_class=3), "
        " count(*) FILTER (WHERE quad_class IN (1,2)), count(*) "
        "FROM event_clusters, unnest(country_iso3s) iso WHERE kind='event' AND iso = ANY(:isos) "
        " AND event_date >= :since GROUP BY 1, 2 ORDER BY 2"), {"isos": iso3s, "since": date.today() - timedelta(days=days)}).all()
    out: dict[str, list[dict]] = {i: [] for i in iso3s}
    for i, d, mc, vc, coop, n in rows:
        out[i].append({"date": d.isoformat(), "material_conflict": mc, "verbal_conflict": vc, "cooperation": coop, "total": n})
    return out


@router.get("/countries/{code}", summary="Country intelligence overview")
def country_detail(code: str, s: Session = Depends(get_db), _: Principal = Depends(require_read)):
    c = get_country(s, code)
    rn = region_names(s)
    groups = s.scalars(select(CountryGroup.code).join(CountryGroupMember, CountryGroupMember.group_id == CountryGroup.id)
                       .where(CountryGroupMember.country_id == c.id)).all()
    aliases = s.scalars(select(CountryAlias.alias).where(CountryAlias.country_id == c.id)).all()
    return {
        "country": {**ser_country(c, rn), "groups": groups, "aliases": aliases},
        "indicators": _latest_indicators(s, c.iso3),
        "crises": _country_crises(s, c.iso3),
        "relationships": _relationships(s, c.id),
        "timeline": daily_series(s, [c.iso3])[c.iso3],
        "modules": {"geopolitics": True, "economy": {"status": "planned", "enabled": False}},
    }


@router.get("/countries/{code}/events", summary="Events involving a country, filterable by role")
def country_events(code: str, role: str | None = Query(None, description="occurred_in | actor1_affiliation | "
                   "actor2_affiliation | government_involved | non_state_cross_border"),
                   category: str | None = None, start: date | None = None, end: date | None = None,
                   min_severity: float = 0.0, p: Pagination = Depends(), s: Session = Depends(get_db),
                   _: Principal = Depends(require_read)):
    c = get_country(s, code)
    st, en = default_range(start, end, days=7)
    roles = select(EventCountryRole.event_id).where(EventCountryRole.country_id == c.id)
    if role:
        roles = roles.where(EventCountryRole.role == role)
    q = select(NormalizedGeopoliticalEvent).where(NormalizedGeopoliticalEvent.id.in_(roles),
                                                  NormalizedGeopoliticalEvent.available_at.between(st, en),
                                                  NormalizedGeopoliticalEvent.severity >= min_severity)
    if category:
        q = q.where(NormalizedGeopoliticalEvent.category == category)
    iso_by_id = dict(s.execute(select(Country.id, Country.iso3)).all())
    return paginate(s, q.order_by(desc(NormalizedGeopoliticalEvent.available_at)), p, lambda e: ser_event(e, iso_by_id))


@router.get("/countries/{code}/analysis", summary="Latest AI country analysis (if an AI provider is configured)")
def country_analysis(code: str, s: Session = Depends(get_db), _: Principal = Depends(require_read)):
    from app.api.v1.analysis import analysis_response

    c = get_country(s, code)
    a = s.scalar(select(GeopoliticalAnalysis).where(GeopoliticalAnalysis.scope_type == "country",
                                                    GeopoliticalAnalysis.scope_key == c.iso3)
                 .order_by(desc(GeopoliticalAnalysis.created_at)).limit(1))
    return analysis_response(a)


class CompareRequest(BaseModel):
    countries: list[str] = Field(..., min_length=1, max_length=MAX_COMPARE, description="ISO3/ISO2 codes")
    start: date | None = None
    end: date | None = None


@router.post("/countries/compare", summary="Compare any number of countries (graph-based, not pair-only)")
def compare(body: CompareRequest, s: Session = Depends(get_db), _: Principal = Depends(require_read)):
    cs = parse_countries(s, body.countries)
    isos = [c.iso3 for c in cs]
    ids = {c.iso3: c.id for c in cs}
    st, en = default_range(body.start, body.end, days=30)
    rn = region_names(s)
    per_country = []
    for c in cs:
        stats = s.execute(text(
            "SELECT count(*), count(*) FILTER (WHERE quad_class=4), count(*) FILTER (WHERE quad_class=3), "
            " count(*) FILTER (WHERE quad_class IN (1,2)), count(*) FILTER (WHERE verification_status='multi_source_reported') "
            "FROM event_clusters WHERE kind='event' AND :iso = ANY(country_iso3s) AND last_seen_at BETWEEN :s AND :e"),
            {"iso": c.iso3, "s": st, "e": en}).one()
        per_country.append({**ser_country(c, rn), "event_clusters": stats[0], "material_conflict": stats[1],
                            "verbal_conflict": stats[2], "cooperation": stats[3], "multi_source_clusters": stats[4],
                            "indicators": _latest_indicators(s, c.iso3)})
    # direct documented interactions between every selected pair (graph edges)
    edges = []
    for a, b in combinations(isos, 2):
        row = s.execute(text(
            "SELECT count(DISTINCT cluster_id), count(DISTINCT cluster_id) FILTER (WHERE quad_class=4), "
            " count(DISTINCT cluster_id) FILTER (WHERE quad_class=3), count(DISTINCT cluster_id) FILTER (WHERE quad_class IN (1,2)), "
            " avg(goldstein) FROM normalized_geopolitical_events WHERE available_at BETWEEN :s AND :e AND "
            " ((actor1_country_id=:a AND actor2_country_id=:b) OR (actor1_country_id=:b AND actor2_country_id=:a))"),
            {"a": ids[a], "b": ids[b], "s": st, "e": en}).one()
        if row[0]:
            edges.append({"source": a, "target": b, "interaction_clusters": row[0], "material_conflict": row[1],
                          "verbal_conflict": row[2], "cooperation": row[3],
                          "avg_goldstein": round(row[4], 2) if row[4] is not None else None})
    names, regions = country_names(s), region_names(s)
    shared = []
    for cr in s.scalars(select(Crisis).where(Crisis.status.in_(["candidate", "active", "dormant"]))
                        .order_by(desc(Crisis.current_priority))):
        members = set(cr.core_country_iso3s or []) | set(cr.location_country_iso3s or [])
        overlap = sorted(members & set(isos))
        if len(overlap) >= (2 if len(isos) > 1 else 1):
            shared.append({**ser_crisis_card(cr, regions.get(cr.region_id), names), "selected_countries_involved": overlap})
    sample_q = (select(NormalizedGeopoliticalEvent)
                .where(NormalizedGeopoliticalEvent.available_at.between(st, en),
                       NormalizedGeopoliticalEvent.actor1_country_id.in_(ids.values()),
                       NormalizedGeopoliticalEvent.actor2_country_id.in_(ids.values()),
                       NormalizedGeopoliticalEvent.actor1_country_id != NormalizedGeopoliticalEvent.actor2_country_id)
                .order_by(desc(NormalizedGeopoliticalEvent.severity), desc(NormalizedGeopoliticalEvent.num_sources)).limit(25))
    iso_by_id = {v: k for k, v in ids.items()}
    return {
        "countries": per_country, "window": {"start": iso(st), "end": iso(en)},
        "interaction_graph": {"nodes": [{"id": i, "name": names.get(i, i)} for i in isos], "edges": edges},
        "shared_crises": shared, "timelines": daily_series(s, isos),
        "interaction_evidence": [ser_event(e, iso_by_id) for e in s.scalars(sample_q)],
        "note": "Interactions are machine-extracted, source-reported events; not independently verified.",
    }
