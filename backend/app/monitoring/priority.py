"""Explainable Monitoring Priority Engine (``priority-v1``).

The score is a **review priority** (0–100) for analysts — NOT a probability of
war or a validated risk estimate. Media coverage volume is never used on its
own: corroboration counts *distinct independent outlets* (capped), and change
is measured on deduplicated event clusters, not mentions.

Components (each normalized to 0..1, weights configurable):

* ``severity`` — CAMEO severity of material-conflict clusters in the last 24h
* ``change_vs_baseline`` — z-score of today's conflict clusters at the crisis
  locations vs. the prior baseline window; falls back to a damped 24h-vs-24h
  comparison (flagged) when baseline history is insufficient
* ``corroboration`` — distinct source domains (log scaled, capped at 30)
* ``diplomatic_signals`` — demands/rejections/threats/reduced relations
* ``evidence_completeness`` — geolocation, actor coding, multi-source share
* ``freshness`` — exponential decay since the last attached evidence (12h half-life)
* ``new_development`` — new crisis (<48h) or newly observed participant (<24h)
"""

from __future__ import annotations

import math
import statistics
from dataclasses import dataclass
from datetime import datetime, timedelta

from sqlalchemy import and_, func, select, text
from sqlalchemy.orm import Session

from app.core.config import get_settings
from app.core.db import utcnow
from app.core.runtime_settings import get_value
from app.crises.models import Crisis, CrisisEvent, CrisisSnapshot
from app.geopolitics.models import VERIFICATION_MULTI_SOURCE, EventCluster
from app.monitoring.models import MonitoringPriority

ALGORITHM_VERSION = "priority-v1"
LEVEL_ORDER = ["low", "elevated", "high", "critical"]
DIPLOMATIC_ROOTS = {"10", "11", "12", "13", "16"}
COOP_ROOTS = {"03", "04", "05", "06", "07", "08"}
MIN_BASELINE_DAYS = 7


POLITICAL_TYPES = {
    "government", "military", "rebel", "insurgent", "separatist", "armed_group", "radical_group", "intelligence",
    "igo", "opposition", "unidentified_state_actor",
}


@dataclass
class WindowCluster:
    id: int
    root: str | None
    quad: int | None
    severity: float
    members: int
    domains: list[str]
    lat: float | None
    countries: list[str]
    multi: bool
    last_seen: datetime
    title: str | None
    political: bool = False


def level_for(score: float, levels: dict[str, float]) -> str:
    for name in ("critical", "high", "elevated"):
        if score >= levels.get(name, 101):
            return name
    return "low"


def _clusters(session: Session, crisis_id: int, start: datetime, end: datetime) -> list[WindowCluster]:
    rows = session.execute(
        select(EventCluster.id, EventCluster.event_root_code, EventCluster.quad_class, EventCluster.max_severity,
               EventCluster.member_count, EventCluster.source_domains, EventCluster.latitude, EventCluster.country_iso3s,
               EventCluster.verification_status, EventCluster.last_seen_at, EventCluster.title)
        .join(CrisisEvent, CrisisEvent.event_cluster_id == EventCluster.id)
        .where(CrisisEvent.crisis_id == crisis_id, EventCluster.last_seen_at > start, EventCluster.last_seen_at <= end)
    ).all()
    out = [WindowCluster(r[0], r[1], r[2], r[3] or 0.0, r[4] or 0, r[5] or [], r[6], r[7] or [],
                         r[8] == VERIFICATION_MULTI_SOURCE, r[9], r[10]) for r in rows]
    if out:
        pol = set(session.scalars(text(
            "SELECT DISTINCT e.cluster_id FROM normalized_geopolitical_events e "
            "JOIN actors a ON a.id IN (e.actor1_id, e.actor2_id) "
            "WHERE e.cluster_id = ANY(:ids) AND a.actor_type = ANY(:types)"
        ), {"ids": [c.id for c in out], "types": sorted(POLITICAL_TYPES)}).all())
        for c in out:
            c.political = c.id in pol or len(c.countries) >= 2
    return out


def _window_has_data(session: Session, start: datetime, end: datetime) -> bool:
    return bool(session.scalar(text(
        "SELECT count(*) FROM ingestion_batches WHERE status='done' AND batch_time > :s AND batch_time <= :e"
    ), {"s": start, "e": end}))


def window_coverage(session: Session, start: datetime, end: datetime) -> float:
    """Share of the 15-minute GDELT batches in [start, end) that were ingested (0..1).

    A trend compares the last 24h with the days before; if the earlier days were not (fully) ingested, e.g. right
    after installation or after downtime, the comparison would show a jump everywhere, so it is skipped."""
    expected = max(1, int((end - start).total_seconds() // 900))
    got = session.scalar(text(
        "SELECT count(DISTINCT batch_time) FROM ingestion_batches WHERE status='done' AND batch_time > :s AND batch_time <= :e"
    ), {"s": start, "e": end}) or 0
    return min(1.0, got / expected)


MIN_TREND_COVERAGE = 0.8


def _intensity(cls: list[WindowCluster]) -> float:
    return sum(c.severity for c in cls if c.quad in (3, 4))


def daily_conflict_counts(session: Session, locations: list[str], end: datetime, days: int) -> list[int]:
    """Conflict-coded clusters per day located in ``locations`` (baseline, independent of crisis membership).

    Uses ``first_seen_at`` (availability) so the baseline only reflects data
    that was actually available at each point in time.
    """
    if not locations:
        return []
    start = end - timedelta(days=days)
    rows = session.execute(
        text(
            "SELECT floor(extract(epoch FROM (:end - first_seen_at)) / 86400)::int AS d, count(*) "
            "FROM event_clusters WHERE kind='event' AND quad_class IN (3,4) "
            "AND action_country_iso3 = ANY(:locs) AND first_seen_at > :start AND first_seen_at <= :end "
            "GROUP BY 1"
        ),
        {"end": end, "start": start, "locs": locations},
    ).all()
    counts = [0] * days
    for d, n in rows:
        if 0 <= d < days:
            counts[d] = n
    return counts  # index 0 = most recent 24h


def data_coverage_days(session: Session, end: datetime, days: int) -> int:
    """Number of past days for which any GDELT data was ingested (to judge baseline sufficiency)."""
    start = end - timedelta(days=days)
    return session.scalar(
        text("SELECT count(DISTINCT date_trunc('day', batch_time)) FROM ingestion_batches "
             "WHERE status='done' AND batch_time > :s AND batch_time <= :e"),
        {"s": start, "e": end},
    ) or 0


def score_crisis(session: Session, crisis: Crisis, now: datetime | None = None) -> MonitoringPriority:
    now = now or utcnow()
    settings = get_settings()
    weights: dict[str, float] = get_value(session, "priority_weights")
    levels: dict[str, float] = get_value(session, "priority_levels")

    w24 = _clusters(session, crisis.id, now - timedelta(hours=24), now)
    prev24 = _clusters(session, crisis.id, now - timedelta(hours=48), now - timedelta(hours=24))
    prior72 = _clusters(session, crisis.id, now - timedelta(hours=96), now - timedelta(hours=24))
    comps: dict[str, dict] = {}

    # severity
    # Clusters involving state/military/armed-group actors (or two countries) count fully;
    # others (e.g. domestic crime-like reports) count at 0.35. Combines how severe the
    # worst corroborated incidents are with (sub-linear) breadth.
    material = [c for c in w24 if c.quad == 4]
    def _w(c: WindowCluster) -> float:
        return c.severity * (1.0 if c.political else 0.35) * (1.0 if c.multi or c.members >= 3 else 0.6)
    weighted = sorted((_w(c) for c in material), reverse=True)
    top = statistics.fmean(weighted[:5]) if weighted else 0.0
    breadth = 1 - math.exp(-sum(weighted) / 25)
    comps["severity"] = {"value": 0.6 * top + 0.4 * breadth, "inputs": {
        "material_conflict_clusters_24h": len(material),
        "political_actor_material_clusters_24h": sum(1 for c in material if c.political),
        "top5_weighted_severity": round(top, 3), "weighted_severity_sum": round(sum(weighted), 3),
        "max_cluster_severity": max((c.severity for c in w24), default=0.0)}}

    # change vs baseline
    locs = sorted(crisis.location_country_iso3s or [])
    counts = daily_conflict_counts(session, locs, now, settings.baseline_days + 1)
    coverage = data_coverage_days(session, now - timedelta(days=1), settings.baseline_days)
    today = counts[0] if counts else 0
    hist = counts[1:] if counts else []
    if coverage >= MIN_BASELINE_DAYS and hist:
        mean = statistics.fmean(hist)
        std = statistics.pstdev(hist) or 1.0
        z = (today - mean) / std
        comps["change_vs_baseline"] = {"value": max(0.0, min(1.0, z / 3)), "inputs": {
            "conflict_clusters_24h": today, "baseline_mean": round(mean, 2), "baseline_std": round(std, 2),
            "zscore": round(z, 2), "baseline_days": len(hist), "method": "zscore"}}
    else:
        prev = len([c for c in prev24 if c.quad in (3, 4)])
        cur = len([c for c in w24 if c.quad in (3, 4)])
        note = f"Insufficient baseline history ({coverage} days of data; {MIN_BASELINE_DAYS} required)."
        if _window_has_data(session, now - timedelta(hours=48), now - timedelta(hours=24)):
            rel = (cur - prev) / max(prev, 3)
            value, method = max(0.0, min(1.0, rel)) * 0.5, "short_term_damped"
        else:
            value, method = 0.0, "unavailable"
            note += " No ingested data for the previous 24h window, so no change can be measured."
        comps["change_vs_baseline"] = {"value": value, "inputs": {
            "conflict_clusters_24h": cur, "conflict_clusters_prev_24h": prev, "method": method, "note": note}}

    # corroboration — distinct outlets, capped so volume alone cannot dominate
    domains = {d for c in w24 for d in c.domains}
    comps["corroboration"] = {"value": min(1.0, math.log1p(len(domains)) / math.log1p(100)),
                              "inputs": {"independent_sources_24h": len(domains)}}

    # diplomatic signals
    dip = [c for c in w24 if c.root in DIPLOMATIC_ROOTS]
    dip_pol = [c for c in dip if c.political]
    comps["diplomatic_signals"] = {"value": len(dip_pol) / (len(dip_pol) + 15), "inputs": {
        "diplomatic_signal_clusters_24h": len(dip), "with_political_actors": len(dip_pol)}}

    # completeness
    if w24:
        compl = statistics.fmean(
            (int(c.lat is not None) + int(len(c.countries) >= 2 or c.members >= 2) + int(c.multi)) / 3 for c in w24)
    else:
        compl = 0.0
    multi_share = (sum(c.multi for c in w24) / len(w24)) if w24 else 0.0
    comps["evidence_completeness"] = {"value": compl, "inputs": {"clusters_24h": len(w24),
                                                                 "multi_source_share": round(multi_share, 3)}}

    # freshness
    hours = max(0.0, (now - crisis.last_event_at).total_seconds() / 3600) if crisis.last_event_at else 999
    comps["freshness"] = {"value": math.exp(-math.log(2) * hours / 12), "inputs": {"hours_since_last_evidence": round(hours, 2)}}

    # new development
    new_crisis = (now - crisis.first_observed_at) < timedelta(hours=48)
    lpa = (crisis.meta or {}).get("last_participant_added_at")
    new_participant = bool(lpa) and (now - datetime.fromisoformat(lpa)) < timedelta(hours=24)
    comps["new_development"] = {"value": 1.0 if (new_crisis or new_participant) else 0.0,
                                "inputs": {"new_crisis": new_crisis, "new_participant": new_participant}}

    total_w = sum(weights.get(k, 0) for k in comps) or 1.0
    for k, c in comps.items():
        c["value"] = round(c["value"], 4)
        c["weight"] = weights.get(k, 0.0)
        c["contribution"] = round(100 * c["weight"] * c["value"] / total_w, 2)
    score = round(sum(c["contribution"] for c in comps.values()), 2)
    level = level_for(score, levels)

    # direction (escalation / de-escalation) — explicit, documented rule
    i24 = _intensity(w24)
    iprior = _intensity(prior72) / 3
    coop24 = sum(1 for c in w24 if c.root in COOP_ROOTS)
    coop_prior = sum(1 for c in prior72 if c.root in COOP_ROOTS) / 3
    deesc_codes = _de_escalation_events(session, [c.id for c in w24])
    cov_now = window_coverage(session, now - timedelta(hours=24), now)
    cov_prior = window_coverage(session, now - timedelta(hours=96), now - timedelta(hours=24))
    comparable = cov_now >= MIN_TREND_COVERAGE and cov_prior >= MIN_TREND_COVERAGE
    if not comparable:
        direction = "stable"  # not enough ingested history on one side to call a trend
    elif i24 >= 1.5 and i24 > 1.5 * max(iprior, 0.5) and prior72:
        direction = "escalating"
    elif prior72 and iprior >= 1.0 and i24 < 0.6 * iprior and (coop24 >= coop_prior or deesc_codes > 0):
        direction = "de_escalating"
    else:
        direction = "stable"
    direction_inputs = {"conflict_intensity_24h": round(i24, 2), "conflict_intensity_prior_daily_avg": round(iprior, 2),
                        "cooperation_clusters_24h": coop24, "cooperation_prior_daily_avg": round(coop_prior, 2),
                        "de_escalation_coded_events_24h": deesc_codes,
                        "data_coverage_24h": round(cov_now, 2), "data_coverage_prior_72h": round(cov_prior, 2)}

    corr = comps["corroboration"]["value"]
    confidence = round(0.5 * corr + 0.3 * multi_share + 0.2 * compl, 3)
    conf_label = "high" if confidence >= 0.7 else "medium" if confidence >= 0.4 else "low"

    triggering = []
    for k, c in sorted(comps.items(), key=lambda kv: -kv[1]["contribution"]):
        if c["value"] >= 0.5 or (score and c["contribution"] >= 0.2 * score):
            triggering.append({"indicator": k, "value": c["value"], "contribution": c["contribution"], **c["inputs"]})
    if direction != "stable":
        triggering.append({"indicator": f"direction:{direction}", **direction_inputs})

    supporting = sorted(w24, key=lambda c: -(c.severity + 0.1) * (1 + math.log1p(c.members)))[:12]
    explanation = _explain(score, level, comps, direction, len(domains), conf_label)

    mp = MonitoringPriority(
        crisis_id=crisis.id, score=score, level=level, direction=direction,
        components={**comps, "_direction": {"value": direction, "inputs": direction_inputs}},
        triggering_indicators=triggering, supporting_event_cluster_ids=[c.id for c in supporting],
        independent_source_count=len(domains), evidence_confidence=confidence, evidence_confidence_label=conf_label,
        explanation=explanation, algorithm_version=ALGORITHM_VERSION, weights=weights, computed_at=now,
    )
    session.add(mp)
    session.flush()

    crisis.current_priority = score
    crisis.priority_level = level
    crisis.direction = direction
    crisis.current_indicators = {
        "clusters_24h": len(w24), "clusters_prev_24h": len(prev24), "material_conflict_24h": len(material),
        "diplomatic_signals_24h": len(dip), "independent_sources_24h": len(domains),
        "evidence_confidence": confidence, "evidence_confidence_label": conf_label,
        "change": comps["change_vs_baseline"]["inputs"], **direction_inputs,
        "priority_id": mp.id, "computed_at": now.isoformat(),
    }
    session.add(CrisisSnapshot(crisis_id=crisis.id, as_of=now, priority=score, priority_level=level, direction=direction,
                               indicators=crisis.current_indicators, participants=[]))
    return mp


def _de_escalation_events(session: Session, cluster_ids: list[int]) -> int:
    if not cluster_ids:
        return 0
    return session.scalar(
        select(func.count()).select_from(text("normalized_geopolitical_events"))
        .where(and_(text("cluster_id = ANY(:ids)"), text("signal_type = 'de_escalation'")))
        .params(ids=cluster_ids)
    ) or 0


def _explain(score: float, level: str, comps: dict, direction: str, sources: int, conf: str) -> str:
    top = sorted(((k, c) for k, c in comps.items()), key=lambda kv: -kv[1]["contribution"])[:3]
    parts = ", ".join(f"{k.replace('_', ' ')} ({c['contribution']:.1f} pts)" for k, c in top if c["contribution"] > 0)
    s = f"Review priority {score:.1f}/100 ({level}). Main drivers: {parts or 'none'}."
    s += f" Evidence from {sources} independent outlets; evidence confidence {conf}."
    if direction != "stable":
        s += f" Observed indicators suggest a {direction.replace('_', '-')} trend."
    s += " Score reflects review priority, not a probability of conflict."
    return s


def score_all(session: Session, now: datetime | None = None) -> list[MonitoringPriority]:
    now = now or utcnow()
    crises = session.scalars(select(Crisis).where(Crisis.status.in_(["candidate", "active", "dormant"])))
    return [score_crisis(session, c, now) for c in crises]
