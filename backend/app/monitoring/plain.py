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
from app.registry.models import Actor, Country

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


LEVELS = ["low", "elevated", "high", "critical"]


def combined_level(news_level: str, deaths_lvl: str) -> str:
    """Severity = verified deaths (UCDP, 12 months), raised by at most one step by today's news.

    News volume alone cannot make a country Critical: big countries are always heavily reported. So a place with
    no verified conflict deaths is at most Moderate, ~100+ deaths at most High, ~1,000+ deaths can reach Critical."""
    base = LEVEL_RANK.get(deaths_lvl, 0)
    news = LEVEL_RANK.get(news_level, 0)
    return LEVELS[max(base, min(news, base + 1))]


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
        level = combined_level(c.priority_level, deaths_level(d) if kind == "conflict" else "low")
        main = kind != "domestic" and (conf["level"] != "low" or d >= 1000)
        countries = sorted(set(c.location_country_iso3s or []) | set(c.core_country_iso3s or []))
        out.append({
            "id": c.id, "uid": c.uid, "headline": headline(c, kind, names, deaths), "kind": kind,
            "kind_label": KIND_LABEL[kind], "severity": {"level": level, "label": SEVERITY_WORD.get(level, "Low")},
            "deaths_12m": d if kind == "conflict" else None,
            "trend": {"code": trend, "label": TREND[trend]}, "reasons": reasons(c, kind, deaths, now), "confidence": conf,
            "group": "main" if main else "other",
            "countries": [{"iso3": x, "name": names.get(x, x)} for x in countries],
            "locations": sorted(c.location_country_iso3s or []),
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


# ----------------------------------------------------------------- by country
DISPUTE_ROOTS = ["10", "11", "12", "13", "16"]
TREND_RANK = {"up": 2, "down": 1, "flat": 0}


def _country_counts(s: Session, isos: list[str], now: datetime) -> dict[str, tuple[int, int]]:
    """(violent reports, threats/disputes) in the last 24h by the country where they happened (deduplicated clusters)."""
    if not isos:
        return {}
    rows = s.execute(text(
        "SELECT action_country_iso3, count(*) FILTER (WHERE quad_class = 4), "
        "count(*) FILTER (WHERE event_root_code = ANY(:dr)) FROM event_clusters "
        "WHERE kind = 'event' AND action_country_iso3 = ANY(:isos) AND last_seen_at >= :t GROUP BY 1"),
        {"isos": isos, "t": now - timedelta(hours=24), "dr": DISPUTE_ROOTS}).all()
    return {iso: (v or 0, d or 0) for iso, v, d in rows}


def _country_activity(s: Session, isos: list[str], now: datetime, days: int = 7) -> dict[str, list[dict]]:
    if not isos:
        return {}
    start = (now - timedelta(days=days - 1)).date()
    rows = s.execute(text(
        "SELECT action_country_iso3, event_date, count(*) FROM event_clusters WHERE kind = 'event' AND quad_class = 4 "
        "AND action_country_iso3 = ANY(:isos) AND event_date >= :d GROUP BY 1, 2"), {"isos": isos, "d": start}).all()
    by: dict[str, dict[date, int]] = {}
    for iso, d, n in rows:
        by.setdefault(iso, {})[d] = n
    dates = [start + timedelta(days=i) for i in range(days)]
    return {iso: [{"date": d.isoformat(), "violent": by.get(iso, {}).get(d, 0)} for d in dates] for iso in isos}


def violence_totals(s: Session, now: datetime | None = None) -> dict:
    """Violent events reported worldwide since the start of yesterday (UTC; deduplicated) and how many countries they
    happened in. Same window and same events as the map's default 'since yesterday' dots: only events with a known
    location (a few news reports name no place, so they cannot be drawn and are not counted either)."""
    now = now or utcnow()
    start = datetime.combine(now.date() - timedelta(days=1), datetime.min.time(), tzinfo=now.tzinfo)
    n, countries = s.execute(text(
        "SELECT count(*), count(DISTINCT action_country_iso3) FROM event_clusters WHERE kind = 'event' "
        "AND quad_class = 4 AND latitude IS NOT NULL AND last_seen_at >= :t"), {"t": start}).one()
    return {"violent_events_24h": n or 0, "countries_with_violence_24h": countries or 0}


def _kind_label(ix: dict | None, deaths: int, fallback: str) -> str:
    """What kind of crisis, from the strongest crisis-index dimension."""
    if not ix:
        return fallback
    d = {k: v for k, v in ix["dimensions"].items() if v is not None and k != "momentum"}
    if not d:
        return fallback
    top = max(d, key=lambda k: d[k] * {"violence": 1.0, "military": 0.75, "tension": 0.75, "unrest": 0.6}[k])
    return {"violence": "Armed conflict" if deaths >= 25 else "Violence", "military": "Military tension",
            "tension": "Political tension", "unrest": "Unrest"}[top]


def _dimension_reasons(ix: dict | None) -> list[str]:
    """Plain sentences for the crisis-index dimensions that stand out (percentile vs all country-months since 1990)."""
    if not ix:
        return []
    d = ix["dimensions"]
    out: list[tuple[float, str]] = []

    def top(v: float) -> str:
        return f"top {max(1, round(100 - v))}% since 1990"

    if (d.get("tension") or 0) >= 85:
        out.append((d["tension"], f"Very tense: threats, sanctions and disputes in the news ({top(d['tension'])})"))
    if (d.get("military") or 0) >= 85:
        out.append((d["military"], f"High military activity: forces, blockades, raids in the news ({top(d['military'])})"))
    if (d.get("unrest") or 0) >= 85:
        out.append((d["unrest"], f"Many protests ({top(d['unrest'])})"))
    m = d.get("momentum")
    if m is not None and m >= 75:
        out.append((m, f"Conflict news is {2 ** ((m - 50) / 50):.1f}× its usual level for this country"))
    elif m is not None and m <= 25:
        out.append((100 - m, f"Calmer than usual lately: conflict news at {2 ** ((m - 50) / 50):.1f}× its normal level"))
    if (ix.get("deaths_12m") or 0) < 25 and (d.get("violence") or 0) >= 60:
        out.append((d["violence"], "Much fighting and attacks reported in the news"))
    return [t for _, t in sorted(out, key=lambda x: -x[0])]


def country_trends(s: Session, sts: list[dict] | None = None) -> dict[str, str]:
    """'up' / 'down' per country, exactly as the country card shows it ('Getting worse' / 'Calming down'): the worst
    trend of the followed situations taking place there (main situations first)."""
    sts = sts if sts is not None else active_stories(s)
    by: dict[str, list[dict]] = {}
    for st in sts:
        for iso in st["locations"]:
            by.setdefault(iso, []).append(st)
    out = {}
    for iso, group in by.items():
        use = [g for g in group if g["group"] == "main"] or group
        t = max((g["trend"]["code"] for g in use), key=lambda x: TREND_RANK[x])
        if t != "flat":
            out[iso] = t
    return out


def country_stories(s: Session, sts: list[dict], now: datetime | None = None) -> list[dict]:
    """One card per country: "Crisis in <country>", with the level from the crisis index (violence, military
    activity, tension, unrest, change vs normal).

    Listed: every country whose index is Moderate or above, plus the countries where followed situations take place
    (those still Low go to 'other signals'). Countries that only take part from outside a situation are shown as
    "also involved"."""
    from app.ml import crisis_index as CI

    now = now or utcnow()
    names = dict(s.execute(select(Country.iso3, Country.name)).all())
    deaths = conflict_deaths_12m(s)
    index = CI.live(s, now.date())
    index_yesterday = CI.live(s, now.date() - timedelta(days=1))
    by: dict[str, list[dict]] = {}
    for st in sts:
        for iso in st["locations"]:
            by.setdefault(iso, []).append(st)
    for iso, ix in index.items():
        if ix["level"] != "low" and iso in names:
            by.setdefault(iso, [])
    isos = sorted(by)
    counts = _country_counts(s, isos, now)
    act = _country_activity(s, isos, now)
    conf_rank = {"low": 0, "medium": 1, "high": 2}
    out = []
    for iso, group in by.items():
        main_group = [g for g in group if g["group"] == "main"]
        use = main_group or group
        name = names.get(iso, iso)
        d = deaths.get(iso, 0)
        ix = index.get(iso)
        level = ix["level"] if ix else "low"
        trend = max((g["trend"]["code"] for g in use), key=lambda x: TREND_RANK[x]) if use else "flat"
        violent, disputes = counts.get(iso, (0, 0))
        conf = (max((g["confidence"] for g in use), key=lambda c: conf_rank.get(c["level"], 0)) if use else
                {"level": "medium", "label": "Partly confirmed",
                 "detail": f"{(ix or {}).get('news_events_30d', 0):,} news events in the last 30 days"})
        yest = (index_yesterday.get(iso) or {}).get("level")
        rs: list[str] = []
        if d >= 25:
            rs.append(f"About {round_count(d)} people killed in organised violence here in the past 12 months (verified by UCDP)")
        rs += _dimension_reasons(ix)
        if trend == "up":
            rs.append("Violence is rising compared with the previous days")
        elif trend == "down":
            rs.append("Fewer violent reports and more talks than in the previous days")
        if violent:
            rs.append(f"{violent} reports of attacks, clashes or use of force here in the last 24 hours")
        if disputes >= 3:
            rs.append(f"{disputes} threats, demands or diplomatic disputes in the last 24 hours")
        involved = sorted({c["iso3"] for g in use for c in g["countries"]} - {iso})
        kind = (min((g["kind"] for g in use), key=lambda k: {"conflict": 0, "tension": 1, "domestic": 2}[k]) if use
                else "conflict" if d >= 25 else "tension")
        out.append({
            "iso3": iso, "name": name, "headline": f"Crisis in {name}", "kind": kind,
            "kind_label": _kind_label(ix, d, KIND_LABEL[kind]),
            "severity": {"level": level, "label": SEVERITY_WORD.get(level, "Low")},
            "index": None if not ix else {"score": ix["score"], "dimensions": ix["dimensions"]},
            "trend": {"code": trend, "label": TREND[trend]}, "reasons": rs[:3], "confidence": conf,
            "group": "main" if level != "low" else "other", "deaths_12m": d or None,
            "violent_24h": violent, "activity": act.get(iso, []), "severity_yesterday": yest,
            "disputes_24h": disputes,
            "situations": [{"id": g["id"], "headline": g["headline"], "severity": g["severity"]}
                           for g in sorted(use, key=sort_key)],
            "also_involved": [{"iso3": x, "name": names.get(x, x)} for x in involved],
        })
    return sorted(out, key=lambda c: (-LEVEL_RANK.get(c["severity"]["level"], 0),
                                      -((c["index"] or {}).get("score") or 0), -c["violent_24h"]))


def what_changed(s: Session, countries: list[dict], now: datetime | None = None, limit: int = 12) -> dict:
    """Countries that got worse or better since yesterday, one line each.

    Only shown when both yesterday and today were fully collected: a level computed while data was still missing
    (right after installation or downtime) would make everything look worse."""
    from app.monitoring.priority import MIN_TREND_COVERAGE, window_coverage

    now = now or utcnow()
    comparable = (window_coverage(s, now - timedelta(hours=48), now - timedelta(hours=24)) >= MIN_TREND_COVERAGE
                  and window_coverage(s, now - timedelta(hours=24), now) >= MIN_TREND_COVERAGE)
    if not comparable:
        return {"items": [], "note": "Changes since yesterday appear here once a full day of news has been collected "
                                     "(about two days after installation or a long pause)."}
    items: list[dict] = []
    for c in countries:
        if c["group"] != "main":
            continue
        prev, cur = c.get("severity_yesterday"), c["severity"]["level"]
        if prev is None:
            items.append({"kind": "new", "iso3": c["iso3"], "text": f"New: {c['headline']}"})
        elif LEVEL_RANK.get(cur, 0) != LEVEL_RANK.get(prev, 0):
            worse = LEVEL_RANK.get(cur, 0) > LEVEL_RANK.get(prev, 0)
            items.append({"kind": "worse" if worse else "better", "iso3": c["iso3"],
                          "text": f"{c['headline']}: {'worse' if worse else 'better'} than yesterday "
                                  f"({SEVERITY_WORD.get(prev, prev)} → {SEVERITY_WORD.get(cur, cur)})"})
        elif c["trend"]["code"] == "up":
            items.append({"kind": "worse", "iso3": c["iso3"], "text": f"{c['headline']}: violence is rising"})
        elif c["trend"]["code"] == "down":
            items.append({"kind": "better", "iso3": c["iso3"], "text": f"{c['headline']}: calming down"})
    order = {"worse": 0, "new": 1, "better": 2}
    items.sort(key=lambda x: order[x["kind"]])
    return {"items": items[:limit], "note": None if items else "No big changes since yesterday."}


def recent_incidents(s: Session, crisis_id: int | None = None, hours: int = 72, limit: int = 8,
                     country_iso3: str | None = None) -> list[dict]:
    """The most important recent incidents of a crisis (or that happened in a country) as short readable
    sentences (≥2 outlets reporting)."""
    since = utcnow() - timedelta(hours=hours)
    q = (select(EventCluster, NormalizedGeopoliticalEvent)
         .outerjoin(NormalizedGeopoliticalEvent, NormalizedGeopoliticalEvent.id == EventCluster.representative_event_id))
    if crisis_id is not None:
        q = q.join(CrisisEvent, CrisisEvent.event_cluster_id == EventCluster.id).where(CrisisEvent.crisis_id == crisis_id)
    if country_iso3 is not None:
        q = q.where(EventCluster.action_country_iso3 == country_iso3)
    q = (q.where(EventCluster.kind == "event", EventCluster.last_seen_at >= since,
                EventCluster.event_root_code.in_(INCIDENT_ROOTS),
                func.cardinality(EventCluster.source_domains) >= 2)
         .order_by(desc(EventCluster.max_severity * func.least(func.cardinality(EventCluster.source_domains), 20)))
         .limit(limit * 4))
    rows = s.execute(q).all()
    texts = incident_texts(s, rows)
    out, seen = [], set()
    for cl, ev in rows:
        text_ = texts.get(cl.id)
        if text_ is None or text_ in seen:
            continue
        seen.add(text_)
        out.append({"id": cl.id, "text": text_, "when": cl.last_seen_at.isoformat() if cl.last_seen_at else None,
                    "outlets": len(cl.source_domains or []), "violent": cl.quad_class == 4,
                    "source_url": ev.source_url if ev else None})
        if len(out) >= limit:
            break
    return out


def incident_texts(s: Session, rows: list) -> dict[int, str | None]:
    """Readable sentence for each (EventCluster, representative NormalizedGeopoliticalEvent | None) pair."""
    events = [ev for _, ev in rows if ev is not None]
    actor_ids = {a for ev in events for a in (ev.actor1_id, ev.actor2_id) if a}
    actors = {aid: (t, cid) for aid, t, cid in s.execute(
        select(Actor.id, Actor.actor_type, Actor.country_id).where(Actor.id.in_(actor_ids))).all()} if actor_ids else {}
    countries = {cid: (iso3, name) for cid, iso3, name in s.execute(select(Country.id, Country.iso3, Country.name)).all()}
    by_iso3 = {iso3: name for iso3, name in countries.values()}
    out: dict[int, str | None] = {}
    for cl, ev in rows:
        if ev is not None:
            d = describe_event(ev, actors, countries)
        else:  # no source-event detail: still say what happened and where
            cname = by_iso3.get(cl.action_country_iso3 or "")
            empty = {"kind": None, "iso3": None, "country": None, "igo": None}
            d = {"a": empty, "b": empty, "place": cname, "place_iso3": cl.action_country_iso3, "place_country": cname}
        out[cl.id] = incident_sentence(cl, d)
    return out


# GDELT actor roles a reader understands. "of" roles belong to a state ("military of Russia"); "in" roles are people
# or groups located in a country ("police in Nigeria"). Other roles (media, business, "unknown", ...) are too vague
# to name and are left out of the sentence.
ROLE_OF = {"government": "government", "military": "military", "intelligence": "intelligence services",
           "legislature": "parliament", "judiciary": "courts"}
ROLE_IN = {"police": "police", "rebel": "rebels", "insurgent": "insurgents", "armed_group": "an armed group",
           "separatist": "separatists", "radical_group": "a radical group", "opposition": "opposition",
           "civilian": "civilians", "refugees": "refugees", "migrants": "migrants", "criminal": "criminals",
           "labor": "workers", "education": "students"}


THE_COUNTRIES = {"Netherlands", "Philippines", "Bahamas", "Gambia", "Maldives", "Comoros", "Czech Republic",
                 "Central African Republic", "Dominican Republic", "Marshall Islands", "Solomon Islands", "Vatican"}
# Roles that do not plausibly carry out armed violence themselves; named as the attacker they are usually a misreading.
NOT_VIOLENT_ROLES = {"judiciary", "legislature", "education", "labor", "refugees", "migrants"}


def the(name: str | None) -> str | None:
    """'United States' -> 'the United States' when a country is named inside a sentence."""
    if name and (name.startswith(("United ", "Democratic Republic")) or name in THE_COUNTRIES):
        return f"the {name}"
    return name


def actor_label(kind: str | None, country: str | None, fallback_country: str | None = None,
                igo_name: str | None = None) -> tuple[str | None, str | None]:
    """(full label, role-only label) for one actor; (None, None) when the actor is too vague to name.

    Actors coded only by nationality are named by their country, never by the raw text: GDELT often uses a capital
    or a city for its country ("Tehran" -> Iran, "Seoul" -> South Korea)."""
    if kind == "igo" and igo_name:
        n = igo_name[4:] if igo_name.lower().startswith("the ") else igo_name
        n = the(n) if n.startswith("United ") else (f"the {n}" if n.startswith("European") else n)
        return n, n
    c = the(country or fallback_country)
    if kind in ("country", "state", "unknown") and country:
        return the(country), None
    if kind in ROLE_OF:
        return (f"the {ROLE_OF[kind]} of {c}" if c else f"the {ROLE_OF[kind]}"), f"the {ROLE_OF[kind]}"
    if kind in ROLE_IN:
        return (f"{ROLE_IN[kind]} in {c}" if c else ROLE_IN[kind]), ROLE_IN[kind]
    return None, None


def short_place(location: str | None, country: str | None) -> str | None:
    """'Kyiv, Kyyiv, Misto, Ukraine' -> 'Kyiv, Ukraine'; 'Bangladesh (general), Bangladesh' -> 'Bangladesh'."""
    if not location:
        return country
    parts = [p.replace("(general)", "").strip() for p in location.split(",")]
    parts = [p for p in parts if p]
    if not parts:
        return country
    first, last = parts[0], country or parts[-1]
    return first if first == last else f"{first}, {last}"


def describe_event(ev: NormalizedGeopoliticalEvent, actors: dict, countries: dict) -> dict:
    def side(aid, ev_country_id, raw_name):
        kind, a_cid = actors.get(aid, (None, None))
        cid = ev_country_id or a_cid
        iso3, name = countries.get(cid, (None, None))
        return {"kind": kind, "iso3": iso3, "country": name,
                "igo": _clean(raw_name) if kind == "igo" else None}

    act_iso3, act_name = countries.get(ev.action_country_id, (None, None))
    return {"a": side(ev.actor1_id, ev.actor1_country_id, ev.actor1_name),
            "b": side(ev.actor2_id, ev.actor2_country_id, ev.actor2_name),
            "place": short_place(ev.action_location, act_name), "place_iso3": act_iso3, "place_country": act_name}


def _clean(name: str | None) -> str | None:
    if not name:
        return None
    n = name.strip()
    return n.title() if n.isupper() else n


def incident_sentence(cl: EventCluster, d: dict | None) -> str | None:
    """One readable line per incident: "<what> in <place> — <side A> against <side B>".

    - Actors are named by role and country, never by GDELT's raw text.
    - Sides from the country where it happened are named by role only ("police", "the government"); when both sides
      are from that one country the line describes an internal event.
    - Actors too vague to name are left out; an incident with neither a place nor a nameable actor is dropped."""
    what = INCIDENT_WORDS.get(cl.event_root_code or "", "Incident")
    if not d:
        return None
    a, b = d["a"], d["b"]
    hostile = cl.quad_class in (3, 4)
    violent = (cl.event_root_code or "") in ("18", "19", "20")
    joiner = " against " if hostile else " and "
    place, place_iso3 = d["place"], d["place_iso3"]

    def name(x):
        if violent and x["kind"] in NOT_VIOLENT_ROLES:
            return None, None, None
        full, role = actor_label(x["kind"], x["country"], d["place_country"], x["igo"])
        iso = x["iso3"] or (place_iso3 if role and x["kind"] != "igo" else None)
        return full, role, iso

    a_full, a_role, a_iso = name(a)
    b_full, b_role, b_iso = name(b)
    foreign = any(f and i and i != place_iso3 for f, i in ((a_full, a_iso), (b_full, b_iso)))

    def short(full, role, iso):
        # A side from the country where it happened is named by role only ("police", "the government"). A bare
        # country adds nothing there, unless the other side is foreign ("Russia against Ukraine").
        if full and iso and iso == place_iso3 and not (role is None and foreign):
            return role
        return full

    a_name, b_name = short(a_full, a_role, a_iso), short(b_full, b_role, b_iso)
    head = what + (f" in {place}" if place else "")
    if not place and a_iso and a_iso == b_iso:
        head = f"{what} inside {the(a['country'] or b['country'])}"
    if a_name and b_name and a_name != b_name:
        return f"{head} — {a_name}{joiner}{b_name}"
    one = a_name or b_name
    if one:
        return f"{head} — {'by' if a_name else 'involving'} {one}"
    return head if place else None
