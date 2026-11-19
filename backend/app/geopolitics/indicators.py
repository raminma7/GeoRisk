"""Country / regional / global tension indicators and country relationship graph.

Indicators are computed on *deduplicated event clusters* (not raw mentions)
and stored as an append-only time series in ``geopolitical_indicators``.
"""

from __future__ import annotations

from datetime import datetime, timedelta

from sqlalchemy import text
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.orm import Session

from app.core.db import utcnow
from app.geopolitics.models import CountryRelationship, GeopoliticalIndicator

ALGORITHM_VERSION = "ind-v1"

COUNTRY_SQL = """
SELECT iso, count(*) FILTER (WHERE quad_class = 4) AS material_conflict,
       count(*) FILTER (WHERE quad_class = 3) AS verbal_conflict,
       count(*) FILTER (WHERE quad_class IN (1,2)) AS cooperation,
       count(*) FILTER (WHERE event_root_code IN ('10','11','12','13','16')) AS diplomatic_signals,
       count(*) FILTER (WHERE event_root_code IN ('18','19','20')) AS violent,
       count(*) AS clusters
FROM event_clusters, unnest(country_iso3s) AS iso
WHERE kind = 'event' AND first_seen_at > :start AND first_seen_at <= :end
GROUP BY iso
"""

REGION_SQL = """
SELECT r.code, count(*) FILTER (WHERE ec.quad_class = 4), count(*) FILTER (WHERE ec.quad_class = 3),
       count(*) FILTER (WHERE ec.quad_class IN (1,2)), count(*) FILTER (WHERE ec.event_root_code IN ('10','11','12','13','16')),
       count(*) FILTER (WHERE ec.event_root_code IN ('18','19','20')), count(*)
FROM event_clusters ec JOIN countries c ON c.iso3 = ec.action_country_iso3 JOIN regions r ON r.id = c.subregion_id
WHERE ec.kind = 'event' AND ec.first_seen_at > :start AND ec.first_seen_at <= :end
GROUP BY r.code
"""

METRICS = ["material_conflict", "verbal_conflict", "cooperation", "diplomatic_signals", "violent", "clusters"]


def _store(session: Session, scope_type: str, scope_key: str, values: dict[str, float], as_of: datetime,
           baselines: dict[str, tuple[float, float]] | None = None) -> None:
    rows = []
    for k, v in values.items():
        mean, std = (baselines or {}).get(k, (None, None))
        z = (v - mean) / std if mean is not None and std else None
        rows.append(dict(scope_type=scope_type, scope_key=scope_key, indicator=k, window="1d", as_of=as_of,
                         value=float(v), baseline_mean=mean, baseline_std=std, zscore=z,
                         algorithm_version=ALGORITHM_VERSION, computed_at=utcnow(), meta={}))
    if rows:
        session.execute(insert(GeopoliticalIndicator).values(rows).on_conflict_do_nothing())


def _baselines(session: Session, scope_type: str, as_of: datetime, days: int = 30) -> dict[tuple[str, str], tuple[float, float]]:
    rows = session.execute(text(
        "SELECT scope_key, indicator, avg(value), stddev_pop(value), count(*) FROM geopolitical_indicators "
        "WHERE scope_type = :st AND \"window\" = '1d' AND as_of < :t - interval '20 hours' AND as_of >= :t - make_interval(days => :d) "
        "GROUP BY scope_key, indicator"
    ), {"st": scope_type, "t": as_of, "d": days}).all()
    return {(k, ind): (m, s) for k, ind, m, s, n in rows if n >= 7 and s}


def compute_indicators(session: Session, as_of: datetime | None = None) -> dict:
    as_of = as_of or utcnow()
    start = as_of - timedelta(days=1)
    stats = {"countries": 0, "regions": 0}
    base = _baselines(session, "country", as_of)
    glob = dict.fromkeys(METRICS, 0)
    for row in session.execute(text(COUNTRY_SQL), {"start": start, "end": as_of}).all():
        iso, vals = row[0], dict(zip(METRICS, row[1:], strict=True))
        _store(session, "country", iso, vals, as_of, {m: base[(iso, m)] for m in METRICS if (iso, m) in base})
        stats["countries"] += 1
    base_r = _baselines(session, "region", as_of)
    for row in session.execute(text(REGION_SQL), {"start": start, "end": as_of}).all():
        code, vals = row[0], dict(zip(METRICS, row[1:], strict=True))
        for m in METRICS:
            glob[m] += vals[m]
        _store(session, "region", code, vals, as_of, {m: base_r[(code, m)] for m in METRICS if (code, m) in base_r})
        stats["regions"] += 1
    base_g = _baselines(session, "global", as_of)
    _store(session, "global", "world", glob, as_of, {m: base_g[("world", m)] for m in METRICS if ("world", m) in base_g})
    stats["relationships"] = compute_relationships(session, as_of)
    session.flush()
    return stats


def compute_relationships(session: Session, as_of: datetime, window_days: int = 7) -> int:
    """Directed actor1-country -> actor2-country interaction edges over a rolling window."""
    start = as_of - timedelta(days=window_days)
    rows = session.execute(text(
        "SELECT actor1_country_id, actor2_country_id, count(DISTINCT cluster_id), "
        " count(DISTINCT cluster_id) FILTER (WHERE quad_class=4), count(DISTINCT cluster_id) FILTER (WHERE quad_class=3), "
        " count(DISTINCT cluster_id) FILTER (WHERE quad_class=1), count(DISTINCT cluster_id) FILTER (WHERE quad_class=2), "
        " avg(goldstein) "
        "FROM normalized_geopolitical_events WHERE available_at > :s AND available_at <= :e "
        " AND actor1_country_id IS NOT NULL AND actor2_country_id IS NOT NULL AND actor1_country_id <> actor2_country_id "
        "GROUP BY 1, 2 HAVING count(DISTINCT cluster_id) >= 2"
    ), {"s": start, "e": as_of}).all()
    if not rows:
        return 0
    values = [dict(source_country_id=a, target_country_id=b, window_start=start.date(), window_end=as_of.date(),
                   window_days=window_days, event_clusters=n, material_conflict=mc, verbal_conflict=vc,
                   verbal_cooperation=vco, material_cooperation=mco, avg_goldstein=g, algorithm_version=ALGORITHM_VERSION,
                   computed_at=utcnow()) for a, b, n, mc, vc, vco, mco, g in rows]
    stmt = insert(CountryRelationship).values(values)
    ex = stmt.excluded
    stmt = stmt.on_conflict_do_update(
        index_elements=["source_country_id", "target_country_id", "window_start", "window_days", "algorithm_version"],
        set_={"event_clusters": ex.event_clusters, "material_conflict": ex.material_conflict,
              "verbal_conflict": ex.verbal_conflict, "verbal_cooperation": ex.verbal_cooperation,
              "material_cooperation": ex.material_cooperation, "avg_goldstein": ex.avg_goldstein,
              "window_end": ex.window_end, "computed_at": ex.computed_at},
    )
    session.execute(stmt)
    return len(values)
