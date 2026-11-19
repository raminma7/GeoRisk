"""Versioned, point-in-time feature snapshots.

Every feature for ``as_of`` is computed only from rows whose availability
timestamp (``first_seen_at`` / ``available_at`` / release date) is
``<= as_of`` — never from data published later. Snapshots are immutable:
a re-run for the same (entity, as_of, feature_set_version) is a no-op.
"""

from __future__ import annotations

from datetime import datetime, time, timedelta, timezone

from sqlalchemy import text
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.orm import Session

from app.core.db import utcnow
from app.geopolitics.normalize import NORMALIZATION_VERSION
from app.ingestion.connectors.ucdp import latest_version
from app.ml.models import FeatureSnapshot

FEATURE_SET_VERSION = "geo-daily-v1"
WINDOWS = (1, 7, 30)

COUNTRY_SQL = """
SELECT iso,
  {cols}
FROM event_clusters, unnest(country_iso3s) iso
WHERE kind='event' AND first_seen_at <= :as_of AND first_seen_at > :as_of - interval '30 days'
GROUP BY iso
"""


def _window_cols(prefix: str = "") -> str:
    parts = []
    for w in WINDOWS:
        cond = f"{prefix}first_seen_at > :as_of - interval '{w} days'"
        parts += [
            f"count(*) FILTER (WHERE {cond}) AS clusters_{w}d",
            f"count(*) FILTER (WHERE {cond} AND {prefix}quad_class=4) AS material_conflict_{w}d",
            f"count(*) FILTER (WHERE {cond} AND {prefix}quad_class=3) AS verbal_conflict_{w}d",
            f"count(*) FILTER (WHERE {cond} AND {prefix}quad_class IN (1,2)) AS cooperation_{w}d",
            f"count(*) FILTER (WHERE {cond} AND {prefix}event_root_code IN ('18','19','20')) AS violent_{w}d",
            f"count(*) FILTER (WHERE {cond} AND {prefix}verification_status='multi_source_reported') AS multi_source_{w}d",
        ]
    return ",\n  ".join(parts)


def _store(session: Session, entity_type: str, key: str, as_of: datetime, feats: dict, vintages: dict) -> int:
    res = session.execute(insert(FeatureSnapshot).values(
        entity_type=entity_type, entity_key=key, as_of=as_of, feature_set_version=FEATURE_SET_VERSION,
        features=feats, source_vintages=vintages, created_at=utcnow(),
    ).on_conflict_do_nothing().returning(FeatureSnapshot.id))
    return len(res.all())


def build_daily_snapshots(session: Session, as_of: datetime | None = None) -> dict:
    if as_of is None:
        as_of = datetime.combine(utcnow().date(), time.min, tzinfo=timezone.utc)
    ucdp = latest_version(session)
    ucdp_ok = ucdp is not None and ucdp.released_at is not None and ucdp.released_at <= as_of
    vintages = {"gdelt_normalization": NORMALIZATION_VERSION, "ucdp_ged_version": ucdp.version if ucdp_ok else None,
                "ucdp_dataset_version_id": ucdp.id if ucdp_ok else None, "pit_rule": "available_at <= as_of"}
    stats = {"as_of": as_of.isoformat(), "country": 0, "country_pair": 0, "crisis": 0, "region": 0, "global": 0}
    p = {"as_of": as_of}

    # countries
    ucdp_feats: dict[str, dict] = {}
    if ucdp_ok:
        for iso, n12, d12, n36, last in session.execute(text(
            "SELECT c.iso3, count(*) FILTER (WHERE u.date_start > (:as_of)::date - 365), "
            " coalesce(sum(u.deaths_best) FILTER (WHERE u.date_start > (:as_of)::date - 365),0), "
            " count(*) FILTER (WHERE u.date_start > (:as_of)::date - 1095), max(u.date_end) "
            "FROM ucdp_events u JOIN countries c ON c.id=u.country_id "
            "WHERE u.dataset_version_id=:dv AND u.available_at <= :as_of AND u.type_of_violence=1 "
            " AND u.date_start <= (:as_of)::date GROUP BY c.iso3"), {**p, "dv": ucdp.id}).all():
            ucdp_feats[iso] = {"ucdp_state_based_events_365d": n12, "ucdp_state_based_deaths_365d": int(d12),
                               "ucdp_state_based_events_1095d": n36,
                               "ucdp_days_since_last_state_based": (as_of.date() - last).days if last else None}
    rows = session.execute(text(COUNTRY_SQL.format(cols=_window_cols())), p).mappings().all()
    crisis_counts = dict(session.execute(text(
        "SELECT iso, count(*) FROM crises, unnest(core_country_iso3s) iso "
        "WHERE first_observed_at <= :as_of AND status IN ('candidate','active','dormant') GROUP BY iso"), p).all())
    seen = set()
    for r in rows:
        iso = r["iso"]
        seen.add(iso)
        feats = {k: v for k, v in r.items() if k != "iso"}
        feats["monitored_crises"] = crisis_counts.get(iso, 0)
        feats.update(ucdp_feats.get(iso, {}))
        stats["country"] += _store(session, "country", iso, as_of, feats, vintages)
    for iso, f in ucdp_feats.items():
        if iso not in seen:
            stats["country"] += _store(session, "country", iso, as_of, {**f, "monitored_crises": crisis_counts.get(iso, 0)},
                                       vintages)

    # country pairs (graph edges)
    for a, b, n, mc, vc, co, g in session.execute(text(
        "SELECT ca.iso3, cb.iso3, count(DISTINCT e.cluster_id), count(DISTINCT e.cluster_id) FILTER (WHERE e.quad_class=4), "
        " count(DISTINCT e.cluster_id) FILTER (WHERE e.quad_class=3), count(DISTINCT e.cluster_id) FILTER (WHERE e.quad_class IN (1,2)), "
        " avg(e.goldstein) FROM normalized_geopolitical_events e "
        "JOIN countries ca ON ca.id = least(e.actor1_country_id, e.actor2_country_id) "
        "JOIN countries cb ON cb.id = greatest(e.actor1_country_id, e.actor2_country_id) "
        "WHERE e.available_at <= :as_of AND e.available_at > :as_of - interval '7 days' "
        " AND e.actor1_country_id <> e.actor2_country_id GROUP BY 1, 2 HAVING count(DISTINCT e.cluster_id) >= 2"), p).all():
        stats["country_pair"] += _store(session, "country_pair", f"{a}-{b}", as_of, {
            "interaction_clusters_7d": n, "material_conflict_7d": mc, "verbal_conflict_7d": vc, "cooperation_7d": co,
            "avg_goldstein_7d": float(g) if g is not None else None}, vintages)

    # crises (multi-country) — from the last priority computed at or before as_of
    for cid, uid, score, comps, direction, conf, isos in session.execute(text(
        "SELECT DISTINCT ON (m.crisis_id) m.crisis_id, c.uid, m.score, m.components, m.direction, m.evidence_confidence, "
        " c.core_country_iso3s FROM monitoring_priorities m JOIN crises c ON c.id=m.crisis_id "
        "WHERE m.computed_at <= :as_of ORDER BY m.crisis_id, m.computed_at DESC"), p).all():
        feats = {"priority_score": score, "direction": direction, "evidence_confidence": conf,
                 "n_core_countries": len(isos or []), "countries": isos,
                 **{f"component_{k}": v.get("value") for k, v in (comps or {}).items() if isinstance(v, dict) and k != "_direction"}}
        stats["crisis"] += _store(session, "crisis", uid, as_of, feats, vintages)

    # regions and global
    reg = session.execute(text(
        "SELECT r.code, " + _window_cols("ec.") + " FROM event_clusters ec "
        "JOIN countries c ON c.iso3 = ec.action_country_iso3 JOIN regions r ON r.id = c.subregion_id "
        "WHERE ec.kind='event' AND ec.first_seen_at <= :as_of AND ec.first_seen_at > :as_of - interval '30 days' "
        "GROUP BY r.code"), p).mappings().all()
    glob: dict[str, int] = {}
    for r in reg:
        feats = {k: v for k, v in r.items() if k != "code"}
        for k, v in feats.items():
            glob[k] = glob.get(k, 0) + (v or 0)
        stats["region"] += _store(session, "region", r["code"], as_of, feats, vintages)
    if glob:
        stats["global"] += _store(session, "global", "world", as_of, glob, vintages)
    session.flush()
    return stats


def backfill_snapshots(session: Session, start: datetime, end: datetime) -> list[dict]:
    out = []
    d = start
    while d <= end:
        out.append(build_daily_snapshots(session, d))
        d += timedelta(days=1)
    return out
