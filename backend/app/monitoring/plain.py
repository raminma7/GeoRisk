"""Plain-language view of the monitoring data for non-specialist readers.

Everything here is rule-based and deterministic (no AI key needed): it turns the crisis engine's
indicators into a short headline, a severity word, a trend, and two or three sentences explaining
*why*, and sorts situations into "main" (armed conflict or tension between countries) and
"other signals" (single-country reports without verified conflict deaths, which are mostly
crime / policing news machine-extracted by GDELT).
"""

from __future__ import annotations

import math
import time
from datetime import date, datetime, timedelta

from sqlalchemy import desc, func, select, text
from sqlalchemy.orm import Session

from app.core.db import utcnow
from app.crises.models import Crisis, CrisisEvent
from app.geopolitics.models import EventCluster, NormalizedGeopoliticalEvent, UcdpEvent
from app.registry.models import Country

ACTIVE = ["candidate", "active"]
LEVEL_RANK = {"low": 0, "elevated": 1, "high": 2, "critical": 3}
SEVERITY_WORD = {"critical": "Critical", "high": "High", "elevated": "Moderate", "low": "Low"}
TREND = {"up": "Getting worse", "down": "Calming down", "flat": "No big change"}
KIND_LABEL = {"conflict": "Armed conflict", "tension": "Tension between countries", "domestic": "Unrest & incidents"}

# What a reader would call each CAMEO root category.
INCIDENT_WORDS = {
    "03": "Offer to cooperate", "04": "Talks", "05": "Diplomatic cooperation", "07": "Aid provided",
    "08": "Step back / concession", "10": "Demands", "11": "Public criticism", "12": "Rejection",
    "13": "Threat", "14": "Protest", "15": "Military build-up", "16": "Relations cut or sanctions",
    "17": "Force used (arrests, raids, blockades)", "18": "Attack", "19": "Fighting", "20": "Mass violence",
}
INCIDENT_ROOTS = ["10", "12", "13", "14", "15", "16", "17", "18", "19", "20", "08"]

_deaths_cache: dict = {"at": 0.0, "value": {}}


def join_names(names: list[str]) -> str:
    names = [n for n in names if n]
    if len(names) <= 1:
        return "".join(names)
    return ", ".join(names[:-1]) + " and " + names[-1]


def round_count(n: int) -> str:
    if n < 100:
        return str(n)
    digits = math.floor(math.log10(n)) - 1
    r = round(n, -digits)
    return f"{r:,}"


def conflict_deaths_12m(s: Session) -> dict[str, int]:
    """Verified deaths from organised violence (UCDP: official + Candidate) in the latest 12 months of data."""
    if time.time() - _deaths_cache["at"] < 3600 and _deaths_cache["value"]:
        return _deaths_cache["value"]
    end = s.scalar(select(func.max(UcdpEvent.date_start)))
    if end is None:
        return {}
    rows = s.execute(select(Country.iso3, func.sum(UcdpEvent.deaths_best))
                     .join(Country, Country.id == UcdpEvent.country_id)
                     .where(UcdpEvent.date_start > end - timedelta(days=365))
                     .group_by(Country.iso3)).all()
    value = {iso3: int(n or 0) for iso3, n in rows}
    _deaths_cache.update(at=time.time(), value=value, through=end)
    return value


def deaths_through() -> date | None:
    return _deaths_cache.get("through")


def _kind(c: Crisis, deaths: dict[str, int]) -> str:
    locs = set(c.location_country_iso3s or [])
    allc = locs | set(c.core_country_iso3s or [])
    if any(deaths.get(x, 0) >= 25 for x in locs):
        return "conflict"
    if len(allc) >= 2:
        return "tension"
    return "domestic"


def headline(c: Crisis, kind: str, names: dict[str, str], deaths: dict[str, int]) -> str:
    locs = sorted(c.location_country_iso3s or [])
    others = sorted(set(c.core_country_iso3s or []) - set(locs))
    n = lambda xs: [names.get(x, x) for x in xs]
    if kind == "conflict":
        fighting = sorted((x for x in locs if deaths.get(x, 0) >= 25), key=lambda x: -deaths.get(x, 0))
        rest = [x for x in locs if x not in fighting] + others
        top = max(deaths.get(x, 0) for x in fighting)
        h = f"{'Armed conflict' if top >= 1000 else 'Armed violence'} in {join_names(n(fighting))}"
        return h + (f", involving {join_names(n(rest[:3]))}" if rest else "")
    if kind == "tension":
        return f"Tensions between {join_names(n(sorted(set(locs) | set(others))[:4]))}"
    return f"Unrest and violence reports in {join_names(n(locs or others))}"


def deaths_level(d: int) -> str:
    """Severity implied by verified deaths in the past year (UCDP: ≥1,000 battle deaths a year is a 'war')."""
    return "critical" if d >= 10_000 else "high" if d >= 1_000 else "elevated" if d >= 100 else "low"


def confidence(ind: dict) -> dict:
    lbl = ind.get("evidence_confidence_label") or "low"
    src = ind.get("independent_sources_24h") or 0
    word = {"high": "Well confirmed", "medium": "Partly confirmed", "low": "Weakly confirmed"}[lbl]
    return {"level": lbl, "label": word, "detail": f"Reported by {src} different news outlets in the last 24 hours"}


def reasons(c: Crisis, kind: str, deaths: dict[str, int], now: datetime) -> list[str]:
    ind = c.current_indicators or {}
    out: list[str] = []
    d = sum(deaths.get(x, 0) for x in (c.location_country_iso3s or []))
    if kind == "conflict" and d >= 25:
        out.append(f"About {round_count(d)} people killed in organised violence in the past 12 months (verified by UCDP)")
    if c.direction == "escalating":
        out.append("Violence is rising compared with the previous days")
    elif c.direction == "de_escalating":
        out.append("Fewer violent reports and more talks than in the previous days")
    if c.first_observed_at and now - c.first_observed_at < timedelta(hours=48) and _system_age(c, now):
        out.append(f"New situation, first noticed {_hours(now - c.first_observed_at)} ago")
    mat = ind.get("material_conflict_24h") or 0
    if mat:
        out.append(f"{mat} reports of attacks, clashes or use of force in the last 24 hours")
    dip = ind.get("diplomatic_signals_24h") or 0
    if dip >= 3:
        out.append(f"{dip} threats, demands or diplomatic disputes in the last 24 hours")
    z = (ind.get("change") or {}).get("zscore")
    if z is not None and z >= 2:
        out.append("Much more activity than is normal for this place")
    return out[:3]


_SYSTEM_START: dict = {}


def _system_age(c: Crisis, now: datetime) -> bool:
    """'New' only means something once monitoring has run for more than two days."""
    start = _SYSTEM_START.get("start")
    return bool(start) and now - start > timedelta(hours=48)


def _hours(td: timedelta) -> str:
    h = int(td.total_seconds() // 3600)
    return f"{h} hours" if h >= 2 else "an hour"


def _trend(c: Crisis, prev_level: str | None) -> str:
    if c.direction == "escalating":
        return "up"
    if c.direction == "de_escalating":
        return "down"
    if prev_level is not None:
        diff = LEVEL_RANK.get(c.priority_level, 0) - LEVEL_RANK.get(prev_level, 0)
        return "up" if diff > 0 else "down" if diff < 0 else "flat"
    return "flat"


def _levels_day_ago(s: Session, ids: list[int], now: datetime) -> dict[int, str]:
    """Level of each crisis at the latest snapshot that is at least ~20 hours old."""
    if not ids:
        return {}
    rows = s.execute(text(
        "SELECT DISTINCT ON (crisis_id) crisis_id, priority_level FROM crisis_snapshots "
        "WHERE crisis_id = ANY(:ids) AND as_of <= :t ORDER BY crisis_id, as_of DESC"),
        {"ids": ids, "t": now - timedelta(hours=20)}).all()
    return dict(rows)


def _daily_activity(s: Session, ids: list[int], now: datetime, days: int = 7) -> dict[int, list[dict]]:
    if not ids:
        return {}
    start = (now - timedelta(days=days - 1)).date()
    rows = s.execute(text(
        "SELECT ce.crisis_id, ec.event_date, count(*) FILTER (WHERE ec.quad_class = 4) "
        "FROM crisis_events ce JOIN event_clusters ec ON ec.id = ce.event_cluster_id "
        "WHERE ce.crisis_id = ANY(:ids) AND ec.event_date >= :d GROUP BY 1, 2"), {"ids": ids, "d": start}).all()
    by: dict[int, dict[date, int]] = {}
    for cid, d, n in rows:
        by.setdefault(cid, {})[d] = n
    dates = [start + timedelta(days=i) for i in range(days)]
    return {cid: [{"date": d.isoformat(), "violent": by.get(cid, {}).get(d, 0)} for d in dates] for cid in ids}


def stories(s: Session, crises: list[Crisis], now: datetime | None = None, with_activity: bool = True) -> list[dict]:
    now = now or utcnow()
    _SYSTEM_START["start"] = s.scalar(select(func.min(Crisis.first_observed_at)))
    names = dict(s.execute(select(Country.iso3, Country.name)).all())
    deaths = conflict_deaths_12m(s)
    ids = [c.id for c in crises]
    prev = _levels_day_ago(s, ids, now)
    act = _daily_activity(s, ids, now) if with_activity else {}
    out = []
    for c in crises:
        kind = _kind(c, deaths)
        conf = confidence(c.current_indicators or {})
        trend = _trend(c, prev.get(c.id))
        d = sum(deaths.get(x, 0) for x in (c.location_country_iso3s or []))
        level = max(c.priority_level, deaths_level(d) if kind == "conflict" else "low", key=lambda x: LEVEL_RANK.get(x, 0))
        main = kind != "domestic" and (conf["level"] != "low" or d >= 1000)
        countries = sorted(set(c.location_country_iso3s or []) | set(c.core_country_iso3s or []))
        out.append({
            "id": c.id, "uid": c.uid, "headline": headline(c, kind, names, deaths), "kind": kind,
            "kind_label": KIND_LABEL[kind], "severity": {"level": level, "label": SEVERITY_WORD.get(level, "Low")},
            "deaths_12m": d if kind == "conflict" else None,
            "trend": {"code": trend, "label": TREND[trend]}, "reasons": reasons(c, kind, deaths, now), "confidence": conf,
            "group": "main" if main else "other",
            "countries": [{"iso3": x, "name": names.get(x, x)} for x in countries],
            "activity": act.get(c.id, []), "last_event_at": c.last_event_at.isoformat() if c.last_event_at else None,
            "level_yesterday": prev.get(c.id), "news_level": c.priority_level,
            # map marker fields
            "title": headline(c, kind, names, deaths), "latitude": c.latitude, "longitude": c.longitude,
            "priority": {"score": c.current_priority, "level": level, "direction": c.direction},
        })
    return out


def sort_key(st: dict) -> tuple:
    return (-LEVEL_RANK.get(st["severity"]["level"], 0), {"conflict": 0, "tension": 1, "domestic": 2}[st["kind"]],
            -(st["priority"]["score"] or 0))


def active_stories(s: Session, now: datetime | None = None) -> list[dict]:
    cs = list(s.scalars(select(Crisis).where(Crisis.status.in_(ACTIVE)).order_by(desc(Crisis.current_priority)).limit(120)))
    return sorted(stories(s, cs, now), key=sort_key)


def what_changed(s: Session, sts: list[dict], now: datetime | None = None, limit: int = 12) -> dict:
    now = now or utcnow()
    items: list[dict] = []
    start = _SYSTEM_START.get("start") or s.scalar(select(func.min(Crisis.first_observed_at)))
    warming_up = start is None or now - start < timedelta(hours=48)
    for st in sts:
        if st["group"] != "main":
            continue
        prev, cur = st["level_yesterday"], st["news_level"]  # compare like with like: news-based level
        if prev and LEVEL_RANK.get(cur, 0) != LEVEL_RANK.get(prev, 0):
            worse = LEVEL_RANK.get(cur, 0) > LEVEL_RANK.get(prev, 0)
            items.append({"kind": "worse" if worse else "better", "crisis_id": st["id"],
                          "text": f"{st['headline']}: {'more' if worse else 'less'} activity than yesterday "
                                  f"({SEVERITY_WORD.get(prev, prev)} → {SEVERITY_WORD.get(cur, cur)} news intensity)"})
        elif st["trend"]["code"] == "up":
            items.append({"kind": "worse", "crisis_id": st["id"], "text": f"{st['headline']}: violence is rising"})
        elif st["trend"]["code"] == "down":
            items.append({"kind": "better", "crisis_id": st["id"], "text": f"{st['headline']}: calming down"})
    if not warming_up:
        for c in s.scalars(select(Crisis).where(Crisis.status.in_(ACTIVE), Crisis.first_observed_at >= now - timedelta(hours=48))
                           .order_by(desc(Crisis.current_priority))):
            st = next((x for x in sts if x["id"] == c.id), None)
            if st and st["group"] == "main":
                items.append({"kind": "new", "crisis_id": c.id, "text": f"New situation: {st['headline']}"})
    order = {"worse": 0, "new": 1, "better": 2}
    items.sort(key=lambda x: order[x["kind"]])
    note = None
    if warming_up:
        note = "Monitoring started less than two days ago. Day-to-day changes will appear here once there is a day to compare with."
    elif not items:
        note = "No big changes since yesterday."
    return {"items": items[:limit], "note": note}


def recent_incidents(s: Session, crisis_id: int, hours: int = 72, limit: int = 8) -> list[dict]:
    """The most important recent incidents of a crisis as short readable sentences (≥2 outlets reporting)."""
    since = utcnow() - timedelta(hours=hours)
    q = (select(EventCluster, NormalizedGeopoliticalEvent)
         .join(CrisisEvent, CrisisEvent.event_cluster_id == EventCluster.id)
         .outerjoin(NormalizedGeopoliticalEvent, NormalizedGeopoliticalEvent.id == EventCluster.representative_event_id)
         .where(CrisisEvent.crisis_id == crisis_id, EventCluster.kind == "event", EventCluster.last_seen_at >= since,
                EventCluster.event_root_code.in_(INCIDENT_ROOTS),
                func.cardinality(EventCluster.source_domains) >= 2)
         .order_by(desc(EventCluster.max_severity * func.least(func.cardinality(EventCluster.source_domains), 20)))
         .limit(limit * 3))
    out, seen = [], set()
    for cl, ev in s.execute(q).all():
        text_ = incident_sentence(cl, ev)
        if text_ in seen:
            continue
        seen.add(text_)
        out.append({"id": cl.id, "text": text_, "when": cl.last_seen_at.isoformat() if cl.last_seen_at else None,
                    "outlets": len(cl.source_domains or []), "violent": cl.quad_class == 4,
                    "source_url": ev.source_url if ev else None})
        if len(out) >= limit:
            break
    return out


def _clean(name: str | None) -> str | None:
    if not name:
        return None
    n = name.strip()
    return n.title() if n.isupper() else n


def incident_sentence(cl: EventCluster, ev: NormalizedGeopoliticalEvent | None) -> str:
    what = INCIDENT_WORDS.get(cl.event_root_code or "", "Incident")
    a1 = _clean(ev.actor1_name) if ev else None
    a2 = _clean(ev.actor2_name) if ev else None
    place = _clean(ev.action_location) if ev else None
    s = what + (f" in {place}" if place else "")
    if a1 and a2 and a1 != a2:
        s += f" — {a1} against {a2}" if cl.quad_class in (3, 4) else f" — {a1} and {a2}"
    elif a1 or a2:
        s += f" — involving {a1 or a2}"
    return s
