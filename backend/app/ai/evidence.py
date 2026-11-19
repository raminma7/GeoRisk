"""Evidence aggregation and prompt-injection defenses.

Only deduplicated, relevance-filtered evidence reaches the LLM — never every
article. External text (headlines, actor names, locations) is treated as
untrusted: control characters are stripped, length is capped, instruction-like
phrases are neutralized, and everything is passed as JSON data inside a
delimited block that the system prompt declares non-executable.
"""

from __future__ import annotations

import hashlib
import json
import re
import unicodedata
from dataclasses import dataclass, field
from datetime import datetime, timedelta

from sqlalchemy import desc, func, select
from sqlalchemy.orm import Session

from app.core.config import get_settings
from app.core.db import utcnow
from app.crises.models import Crisis, CrisisArticle, CrisisEvent, CrisisEvidenceLog
from app.geopolitics import cameo
from app.geopolitics.models import EventCluster, NewsArticle, NormalizedGeopoliticalEvent, UcdpEvent
from app.ingestion.models import DataSource, SourcePermission
from app.registry.models import Country

INJECTION_PATTERNS = re.compile(
    r"(ignore (all |any )?(previous|prior|above) (instructions|prompts?)|disregard (the )?(system|previous)|"
    r"you are now|new instructions|system prompt|developer mode|</?(system|assistant|user|untrusted_evidence)>|"
    r"act as|jailbreak|do anything now|output the following|respond only with)",
    re.IGNORECASE,
)


def sanitize(text: str | None, max_len: int = 300) -> str:
    if not text:
        return ""
    t = unicodedata.normalize("NFKC", str(text))
    t = "".join(ch for ch in t if unicodedata.category(ch)[0] != "C" or ch == " ")
    t = INJECTION_PATTERNS.sub("[removed]", t)
    t = t.replace("```", "'''")
    t = re.sub(r"\s+", " ", t).strip()
    return t[:max_len]


@dataclass
class EvidenceBundle:
    scope: str
    items: list[dict] = field(default_factory=list)
    context: dict = field(default_factory=dict)

    def ids(self) -> set[str]:
        return {i["id"] for i in self.items}

    def url_for(self, eid: str) -> str | None:
        for i in self.items:
            if i["id"] == eid:
                return i.get("source_url")
        return None

    def evidence_ids(self) -> dict:
        return {
            "event_clusters": [int(i["id"][1:]) for i in self.items if i["id"].startswith("E")],
            "articles": [int(i["id"][1:]) for i in self.items if i["id"].startswith("A")],
            "ucdp_events": [i["id"][1:] for i in self.items if i["id"].startswith("U")],
        }

    def input_hash(self) -> str:
        payload = json.dumps({"scope": self.scope, "ids": sorted(self.ids()), "ctx": self.context}, sort_keys=True, default=str)
        return hashlib.sha256(payload.encode()).hexdigest()

    def render(self) -> str:
        return json.dumps({"context": self.context, "evidence": self.items}, ensure_ascii=False, default=str, indent=0)


def _ai_allowed_sources(session: Session) -> set[str]:
    rows = session.execute(select(DataSource.key).join(SourcePermission, SourcePermission.data_source_id == DataSource.id)
                           .where(SourcePermission.allow_ai_processing.is_(True))).all()
    return {r[0] for r in rows}


def _cluster_items(session: Session, cluster_ids: list[int], limit: int) -> list[dict]:
    if not cluster_ids:
        return []
    rows = session.execute(
        select(EventCluster, NormalizedGeopoliticalEvent.source_url, NormalizedGeopoliticalEvent.actor1_name,
               NormalizedGeopoliticalEvent.actor2_name, NormalizedGeopoliticalEvent.action_location,
               NormalizedGeopoliticalEvent.signal_type, NormalizedGeopoliticalEvent.event_code)
        .outerjoin(NormalizedGeopoliticalEvent, NormalizedGeopoliticalEvent.id == EventCluster.representative_event_id)
        .where(EventCluster.id.in_(cluster_ids))
        .order_by(desc(EventCluster.max_severity * func.least(func.cardinality(EventCluster.source_domains) + 1, 10)))
        .limit(limit)
    ).all()
    out = []
    for cl, url, a1, a2, loc, sig, code in rows:
        out.append({
            "id": f"E{cl.id}", "type": "event_cluster", "date": str(cl.event_date),
            "category": cameo.category_label(cl.event_root_code), "cameo_code": code,
            "signal": sig, "actor1": sanitize(a1, 80), "actor2": sanitize(a2, 80), "location": sanitize(loc, 120),
            "countries": cl.country_iso3s, "reports": cl.member_count,
            "independent_sources": len(cl.source_domains or []),
            "verification_status": cl.verification_status, "source_url": url,
            "provenance": "GDELT machine-extracted from news; unverified",
        })
    return out


def _article_items(session: Session, article_ids: list[int], limit: int) -> list[dict]:
    if not article_ids:
        return []
    rows = session.scalars(select(NewsArticle).where(NewsArticle.id.in_(article_ids))
                           .order_by(desc(NewsArticle.seen_at)).limit(limit))
    seen_clusters: set[int] = set()
    out = []
    for a in rows:
        if a.cluster_id and a.cluster_id in seen_clusters:
            continue  # deduplicated: one headline per news cluster
        if a.cluster_id:
            seen_clusters.add(a.cluster_id)
        out.append({"id": f"A{a.id}", "type": "news_headline", "date": a.seen_at.date().isoformat(),
                    "headline": sanitize(a.title, 250), "language": a.language, "outlet": a.domain,
                    "outlet_country": a.source_country_name, "countries": a.country_iso3s, "source_url": a.url,
                    "provenance": "headline only (source-reported claim)"})
    return out


def _ucdp_history(session: Session, iso3s: list[str], years: int = 5) -> dict:
    if not iso3s:
        return {}
    since = (utcnow() - timedelta(days=365 * years)).date()
    rows = session.execute(
        select(Country.iso3, UcdpEvent.type_of_violence, func.count(), func.sum(UcdpEvent.deaths_best),
               func.max(UcdpEvent.date_end))
        .join(Country, Country.id == UcdpEvent.country_id)
        .where(Country.iso3.in_(iso3s), UcdpEvent.date_start >= since)
        .group_by(Country.iso3, UcdpEvent.type_of_violence)
    ).all()
    out: dict = {}
    names = {1: "state_based", 2: "non_state", 3: "one_sided"}
    for iso, tov, n, deaths, last in rows:
        out.setdefault(iso, {})[names.get(tov, str(tov))] = {"events": n, "best_fatality_estimate": int(deaths or 0),
                                                             "latest_event": str(last)}
    return {"source": "UCDP GED (human-coded research dataset)", "window_years": years, "by_country": out} if out else {}


def crisis_bundle(session: Session, crisis: Crisis) -> EvidenceBundle | None:
    limit = get_settings().ai_max_evidence_items
    allowed = _ai_allowed_sources(session)
    since = utcnow() - timedelta(days=7)
    cluster_ids = list(session.scalars(
        select(CrisisEvent.event_cluster_id).join(EventCluster, EventCluster.id == CrisisEvent.event_cluster_id)
        .where(CrisisEvent.crisis_id == crisis.id, EventCluster.last_seen_at >= since)))
    items = _cluster_items(session, cluster_ids, limit) if "gdelt_events" in allowed else []
    if "gdelt_doc" in allowed:
        art_ids = list(session.scalars(select(CrisisArticle.article_id).where(CrisisArticle.crisis_id == crisis.id)))
        items += _article_items(session, art_ids, max(5, limit // 3))
    if not items:
        return None
    countries = sorted(set(crisis.core_country_iso3s or []) | set(crisis.location_country_iso3s or []))
    names = dict(session.execute(select(Country.iso3, Country.name).where(Country.iso3.in_(countries))).all())
    log = session.scalars(select(CrisisEvidenceLog).where(CrisisEvidenceLog.crisis_id == crisis.id)
                          .order_by(desc(CrisisEvidenceLog.created_at)).limit(5))
    ctx = {
        "scope": "crisis", "crisis_uid": crisis.uid, "system_title": sanitize(crisis.title), "status": crisis.status,
        "countries": [{"iso3": c, "name": names.get(c, c)} for c in countries],
        "first_observed_at": crisis.first_observed_at.isoformat(), "last_event_at": crisis.last_event_at.isoformat(),
        "monitoring_indicators": crisis.current_indicators, "priority": {"score": crisis.current_priority,
                                                                         "level": crisis.priority_level,
                                                                         "direction": crisis.direction},
        "recent_system_decisions": [sanitize(x.reason, 300) for x in log],
        "historical_context": _ucdp_history(session, countries) if "ucdp_ged" in allowed else {},
    }
    return EvidenceBundle(scope=f"crisis:{crisis.id}", items=items, context=ctx)


def countries_bundle(session: Session, iso3s: list[str], days: int = 7) -> EvidenceBundle | None:
    limit = get_settings().ai_max_evidence_items
    allowed = _ai_allowed_sources(session)
    since: datetime = utcnow() - timedelta(days=days)
    q = (select(EventCluster.id).where(EventCluster.kind == "event", EventCluster.last_seen_at >= since,
                                       EventCluster.quad_class.in_([1, 2, 3, 4]),
                                       EventCluster.country_iso3s.overlap(iso3s)))
    if len(iso3s) > 1:
        # multi-country: favour clusters that involve at least two of the selected countries
        q = q.where(func.cardinality(EventCluster.country_iso3s) >= 2)
    q = q.order_by(desc(EventCluster.max_severity * func.least(func.cardinality(EventCluster.source_domains) + 1, 10))).limit(limit * 3)
    cluster_ids = list(session.scalars(q))
    items = _cluster_items(session, cluster_ids, limit) if "gdelt_events" in allowed else []
    if "gdelt_doc" in allowed:
        art_ids = list(session.scalars(select(NewsArticle.id).where(NewsArticle.seen_at >= since,
                                                                     NewsArticle.country_iso3s.overlap(iso3s))
                                       .order_by(desc(NewsArticle.seen_at)).limit(200)))
        items += _article_items(session, art_ids, max(5, limit // 3))
    if not items:
        return None
    names = dict(session.execute(select(Country.iso3, Country.name).where(Country.iso3.in_(iso3s))).all())
    crises = session.scalars(select(Crisis).where(Crisis.status.in_(["candidate", "active"]),
                                                  Crisis.core_country_iso3s.overlap(iso3s))
                             .order_by(desc(Crisis.current_priority)).limit(10))
    ctx = {
        "scope": "country" if len(iso3s) == 1 else "multi_country",
        "countries": [{"iso3": c, "name": names.get(c, c)} for c in iso3s], "window_days": days,
        "monitored_crises": [{"uid": c.uid, "title": sanitize(c.title), "priority": c.current_priority,
                              "level": c.priority_level, "direction": c.direction} for c in crises],
        "historical_context": _ucdp_history(session, iso3s) if "ucdp_ged" in allowed else {},
    }
    return EvidenceBundle(scope=("country:" if len(iso3s) == 1 else "multi:") + ",".join(iso3s), items=items, context=ctx)


def global_bundle(session: Session) -> EvidenceBundle | None:
    limit = get_settings().ai_max_evidence_items
    crises = list(session.scalars(select(Crisis).where(Crisis.status.in_(["candidate", "active"]))
                                  .order_by(desc(Crisis.current_priority)).limit(12)))
    if not crises:
        return None
    items = []
    per = max(2, limit // len(crises))
    for c in crises:
        ids = list(session.scalars(select(CrisisEvent.event_cluster_id).where(CrisisEvent.crisis_id == c.id)))
        for it in _cluster_items(session, ids, per):
            it["crisis_uid"] = c.uid
            items.append(it)
    ctx = {"scope": "daily_report", "date": utcnow().date().isoformat(),
           "crises": [{"uid": c.uid, "title": sanitize(c.title), "countries": c.core_country_iso3s,
                       "priority": c.current_priority, "level": c.priority_level, "direction": c.direction,
                       "indicators": c.current_indicators} for c in crises]}
    return EvidenceBundle(scope="daily_report:" + utcnow().date().isoformat(), items=items[: limit * 2], context=ctx)
