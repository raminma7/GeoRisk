"""Automatic crisis discovery and tracking (algorithm ``crisis-v1``).

Deterministic and explainable:

1. Select *relevant* deduplicated event clusters from the lookback window:
   conflict-coded (CAMEO quad 3/4) clusters with political actors, plus
   cooperation/yield clusters (used only to track de-escalation inside
   existing crises — they never create crises).
2. Score each unattached cluster against active crises:
   ``0.45*containment + 0.25*jaccard + 0.30*location_match`` over country
   sets. ``>= attach_threshold`` attaches; ``[review, attach)`` attaches *and*
   flags for human review; lower is left for discovery.
3. Group the remaining conflict clusters by theater (location country) and
   create a candidate crisis when volume, source diversity and severity
   criteria are met. Crises can involve any number of countries; participants
   grow as attached evidence accumulates.

Every decision is written to ``crisis_evidence_log`` with its inputs.
"""

from __future__ import annotations

import math
from collections import Counter, defaultdict
from dataclasses import dataclass, field
from datetime import datetime, timedelta

from sqlalchemy import func, select, text
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.orm import Session

from app.core.config import get_settings
from app.core.db import utcnow
from app.core.logging import get_logger
from app.core.runtime_settings import get_value
from app.crises.models import Crisis, CrisisArticle, CrisisEvent, CrisisEvidenceLog, CrisisParticipant
from app.geopolitics.models import VERIFICATION_MULTI_SOURCE, EventCluster, NewsArticle
from app.registry.models import Country, Region

log = get_logger(__name__)

ALGORITHM_VERSION = "crisis-v1"
POLITICAL_ACTOR_TYPES = {
    "government", "military", "rebel", "insurgent", "separatist", "armed_group", "radical_group",
    "intelligence", "igo", "opposition", "legislature", "unidentified_state_actor", "political_party",
}
COOPERATION_ROOTS = {"03", "04", "05", "06", "07", "08"}
DIPLOMATIC_ROOTS = {"10", "11", "12", "13", "16"}
VIOLENT_ROOTS = {"18", "19", "20"}


@dataclass
class ClusterView:
    id: int
    countries: set[str]
    action: str | None
    root: str | None
    quad: int | None
    severity: float
    members: int
    domains: set[str]
    last_seen: datetime
    lat: float | None
    lon: float | None
    multi_source: bool
    actor_types: set[str] = field(default_factory=set)

    @property
    def is_conflict(self) -> bool:
        return self.quad in (3, 4)

    @property
    def is_cooperation(self) -> bool:
        return self.quad in (1, 2) and (self.root in COOPERATION_ROOTS)

    @property
    def political(self) -> bool:
        return bool(self.actor_types & POLITICAL_ACTOR_TYPES) or len(self.countries) >= 2


@dataclass
class CrisisView:
    crisis: Crisis
    countries: set[str]
    locations: set[str]


def match_score(cl: ClusterView, cv: CrisisView) -> tuple[float, dict]:
    k = cv.countries | cv.locations
    inter = cl.countries & k
    if not inter:
        return 0.0, {}
    containment = len(inter) / len(cl.countries)
    jacc = len(inter) / len(cl.countries | k)
    loc = 1.0 if cl.action and cl.action in cv.locations else 0.0
    score = 0.45 * containment + 0.25 * jacc + 0.30 * loc
    return round(score, 4), {
        "containment": round(containment, 3), "jaccard": round(jacc, 3), "location_match": loc,
        "shared_countries": sorted(inter),
    }


def _load_clusters(session: Session, since: datetime) -> list[ClusterView]:
    rows = session.execute(
        select(
            EventCluster.id, EventCluster.country_iso3s, EventCluster.action_country_iso3, EventCluster.event_root_code,
            EventCluster.quad_class, EventCluster.max_severity, EventCluster.member_count, EventCluster.source_domains,
            EventCluster.last_seen_at, EventCluster.latitude, EventCluster.longitude, EventCluster.verification_status,
        ).where(EventCluster.kind == "event", EventCluster.last_seen_at >= since, func.cardinality(EventCluster.country_iso3s) > 0)
    ).all()
    out = []
    for r in rows:
        cv = ClusterView(r[0], set(r[1] or []), r[2], r[3], r[4], r[5] or 0.0, r[6] or 0, set(r[7] or []), r[8], r[9], r[10],
                         r[11] == VERIFICATION_MULTI_SOURCE)
        if cv.is_conflict or cv.is_cooperation:
            out.append(cv)
    if out:
        types = session.execute(
            text(
                "SELECT e.cluster_id, array_agg(DISTINCT a.actor_type) FROM normalized_geopolitical_events e "
                "JOIN actors a ON a.id = e.actor1_id OR a.id = e.actor2_id "
                "WHERE e.cluster_id = ANY(:ids) GROUP BY e.cluster_id"
            ),
            {"ids": [c.id for c in out]},
        ).all()
        tmap = {cid: set(t or []) for cid, t in types}
        for c in out:
            c.actor_types = tmap.get(c.id, set())
    return out


def _active_crises(session: Session) -> list[CrisisView]:
    crises = session.scalars(select(Crisis).where(Crisis.status.in_(["candidate", "active", "dormant"])))
    return [CrisisView(c, set(c.core_country_iso3s or []), set(c.location_country_iso3s or [])) for c in crises]


def _log(session: Session, crisis: Crisis, action: str, reason: str, evidence: dict) -> None:
    session.add(CrisisEvidenceLog(crisis_id=crisis.id, action=action, reason=reason, evidence=evidence,
                                  algorithm_version=ALGORITHM_VERSION))


class CrisisEngine:
    def __init__(self, session: Session, now: datetime | None = None):
        self.s = session
        self.now = now or utcnow()
        self.settings = get_settings()
        self.attach_threshold = float(get_value(session, "crisis_attach_threshold"))
        self.review_threshold = float(get_value(session, "crisis_review_threshold"))
        self.min_clusters = int(get_value(session, "crisis_min_event_clusters"))
        self.min_sources = int(get_value(session, "crisis_min_sources"))
        self.names = dict(session.execute(select(Country.iso3, Country.name)).all())

    # ------------------------------------------------------------------ main
    def run(self) -> dict:
        since = self.now - timedelta(hours=self.settings.crisis_lookback_hours)
        clusters = _load_clusters(self.s, since)
        attached_ids = set(self.s.scalars(select(CrisisEvent.event_cluster_id).where(
            CrisisEvent.event_cluster_id.in_([c.id for c in clusters]))).all()) if clusters else set()
        pending = [c for c in clusters if c.id not in attached_ids]
        crises = _active_crises(self.s)
        stats = {"clusters_considered": len(pending), "attached": 0, "review_flagged": 0, "created": 0}

        touched: dict[int, list[tuple[ClusterView, float, dict, bool]]] = defaultdict(list)
        leftovers: list[ClusterView] = []
        for cl in pending:
            best, best_score, best_detail = None, 0.0, {}
            for cv in crises:
                sc, detail = match_score(cl, cv)
                if sc > best_score:
                    best, best_score, best_detail = cv, sc, detail
            if best and best_score >= self.attach_threshold:
                touched[best.crisis.id].append((cl, best_score, best_detail, False))
            elif best and best_score >= self.review_threshold and cl.is_conflict and cl.political:
                touched[best.crisis.id].append((cl, best_score, best_detail, True))
            elif cl.is_conflict and cl.political:
                leftovers.append(cl)

        by_id = {cv.crisis.id: cv for cv in crises}
        for cid, items in touched.items():
            self._attach(by_id[cid], items)
            stats["attached"] += len(items)
            stats["review_flagged"] += sum(1 for i in items if i[3])

        for group in self._group_theaters(leftovers):
            cv = self._maybe_create(group, crises)
            if cv is not None:
                crises.append(cv)
                stats["created"] += 1
        self.s.flush()
        stats["merged"] = self.merge_duplicates()
        stats["crises_refreshed"] = self.refresh_all()
        stats["articles_linked"] = self.link_articles()
        self.s.flush()
        return stats

    # ------------------------------------------------------------ discovery
    def _group_theaters(self, clusters: list[ClusterView]) -> list[list[ClusterView]]:
        groups: dict[str, list[ClusterView]] = defaultdict(list)
        for cl in clusters:
            key = cl.action or sorted(cl.countries)[0]
            groups[key].append(cl)
        return sorted(groups.values(), key=lambda g: -sum(c.members for c in g))

    def _signature(self, group: list[ClusterView]) -> tuple[set[str], set[str]]:
        loc_counts = Counter(c.action for c in group if c.action)
        ctry_counts = Counter(x for c in group for x in c.countries)
        n = len(group)
        locations = {loc for loc, k in loc_counts.items() if k >= max(1, 0.25 * n)} or set(list(loc_counts)[:1])
        core = {iso for iso, k in ctry_counts.most_common(5) if k >= max(2, 0.25 * n)} | locations
        return core, locations

    def _maybe_create(self, group: list[ClusterView], crises: list[CrisisView]) -> CrisisView | None:
        core, locations = self._signature(group)
        # merge into an overlapping existing crisis rather than creating a duplicate
        for cv in crises:
            k = cv.countries | cv.locations
            jacc = len(core & k) / len(core | k) if core & k else 0.0
            if jacc >= 0.5 and ((locations & cv.locations) or (len(core) >= 2 and jacc >= 0.66)):
                items = []
                for cl in group:
                    sc, detail = match_score(cl, cv)
                    items.append((cl, sc, {**detail, "via": "theater_merge"}, sc < self.attach_threshold))
                self._attach(cv, items)
                return None
        domains = set().union(*(c.domains for c in group)) if group else set()
        material = [c for c in group if c.quad == 4]
        diplomatic = [c for c in group if c.root in DIPLOMATIC_ROOTS]
        criteria = {
            "event_clusters": len(group), "min_event_clusters": self.min_clusters,
            "independent_sources": len(domains), "min_sources": self.min_sources,
            "material_conflict_clusters": len(material), "diplomatic_signal_clusters": len(diplomatic),
        }
        if len(group) < self.min_clusters or len(domains) < self.min_sources:
            return None
        if len(material) < 2 and len(diplomatic) < 4:
            return None

        first = min(c.last_seen for c in group)
        crisis = Crisis(
            uid="pending", title=self._title(core, locations, group), status="candidate",
            core_country_iso3s=sorted(core), location_country_iso3s=sorted(locations),
            first_observed_at=first, last_event_at=max(c.last_seen for c in group), last_updated_at=self.now,
            algorithm_version=ALGORITHM_VERSION, current_indicators={}, meta={"creation_criteria": criteria},
            region_id=self._region_for(locations),
        )
        self.s.add(crisis)
        self.s.flush()
        crisis.uid = f"CR-{first.year}-{crisis.id:06d}"
        _log(self.s, crisis, "created",
             f"Detected {len(group)} deduplicated conflict-coded event clusters reported by {len(domains)} "
             f"independent outlets in {', '.join(self.names.get(x, x) for x in sorted(locations))}.",
             {"criteria": criteria, "core_countries": sorted(core), "locations": sorted(locations),
              "seed_cluster_ids": [c.id for c in sorted(group, key=lambda c: -c.severity * c.members)[:25]]})
        cv = CrisisView(crisis, set(core), set(locations))
        self._attach(cv, [(cl, match_score(cl, cv)[0], {**match_score(cl, cv)[1], "via": "creation"}, False) for cl in group],
                     log_it=False)
        return cv

    def _title(self, core: set[str], locations: set[str], group: list[ClusterView]) -> str:
        kind = _crisis_type(group)
        label = {"armed_conflict": "Armed conflict activity", "diplomatic": "Diplomatic tensions",
                 "unrest": "Political unrest", "mixed": "Conflict and tension signals"}[kind]
        ordered = sorted(locations) + sorted(core - locations)
        names = [self.names.get(x, x) for x in ordered][:4]
        return f"{label}: {', '.join(names)}"

    def _region_for(self, locations: set[str]) -> int | None:
        if not locations:
            return None
        return self.s.scalar(select(Country.subregion_id).where(Country.iso3 == sorted(locations)[0]))

    # ------------------------------------------------------------- attaching
    def _attach(self, cv: CrisisView, items: list[tuple[ClusterView, float, dict, bool]], log_it: bool = True) -> None:
        if not items:
            return
        rows = [
            {"crisis_id": cv.crisis.id, "event_cluster_id": cl.id, "added_at": self.now, "match_score": sc,
             "reason": detail, "needs_review": review}
            for cl, sc, detail, review in items
        ]
        self.s.execute(insert(CrisisEvent).values(rows).on_conflict_do_nothing())
        crisis = cv.crisis
        crisis.last_event_at = max([crisis.last_event_at, *(cl.last_seen for cl, *_ in items)])
        crisis.last_updated_at = self.now
        if crisis.status == "dormant":
            crisis.status = "active"
            _log(self.s, crisis, "status_changed", "New evidence re-activated a dormant crisis.", {})
        flagged = [cl.id for cl, _, _, review in items if review]
        if flagged:
            crisis.needs_review = True
            _log(self.s, crisis, "review_flagged",
                 f"{len(flagged)} event clusters matched with ambiguous scores "
                 f"({self.review_threshold}–{self.attach_threshold}); human review requested.",
                 {"cluster_ids": flagged[:100]})
        if log_it:
            _log(self.s, crisis, "events_attached", f"Attached {len(items)} event clusters.",
                 {"cluster_ids": [cl.id for cl, *_ in items][:100],
                  "scores": {str(cl.id): sc for cl, sc, *_ in items[:100]}})

    # ----------------------------------------------------------------- merge
    def merge_duplicates(self) -> int:
        """Merge crises describing the same theater (same multi-country core)."""
        crises = list(self.s.scalars(select(Crisis).where(Crisis.status.in_(["candidate", "active", "dormant"]))
                                     .order_by(Crisis.first_observed_at, Crisis.id)))
        merged = 0
        alive: list[Crisis] = []
        for c in crises:
            k = set(c.core_country_iso3s or []) | set(c.location_country_iso3s or [])
            target = None
            for t in alive:
                kt = set(t.core_country_iso3s or []) | set(t.location_country_iso3s or [])
                if len(k) >= 2 and len(kt) >= 2 and len(k & kt) / len(k | kt) >= 0.66:
                    target = t
                    break
            if target is None:
                alive.append(c)
                continue
            self.s.execute(text(
                "INSERT INTO crisis_events (crisis_id, event_cluster_id, added_at, match_score, reason, needs_review) "
                "SELECT :t, event_cluster_id, added_at, match_score, reason || jsonb_build_object('merged_from', :src), "
                "needs_review FROM crisis_events WHERE crisis_id = :src ON CONFLICT DO NOTHING"
            ), {"t": target.id, "src": c.id})
            target.core_country_iso3s = sorted(set(target.core_country_iso3s or []) | set(c.core_country_iso3s or []))
            target.location_country_iso3s = sorted(set(target.location_country_iso3s or []) | set(c.location_country_iso3s or []))
            target.first_observed_at = min(target.first_observed_at, c.first_observed_at)
            c.status = "merged"
            c.meta = {**(c.meta or {}), "merged_into": target.id}
            _log(self.s, target, "merged", f"Merged duplicate crisis {c.uid} (same multi-country theater).",
                 {"merged_crisis_id": c.id, "merged_uid": c.uid})
            _log(self.s, c, "merged", f"Merged into {target.uid}.", {"target_crisis_id": target.id})
            merged += 1
        self.s.flush()
        return merged

    # --------------------------------------------------------------- refresh
    def refresh_all(self) -> int:
        crises = list(self.s.scalars(select(Crisis).where(Crisis.status.in_(["candidate", "active", "dormant"]))))
        for c in crises:
            self.refresh(c)
        return len(crises)

    def refresh(self, crisis: Crisis) -> None:
        """Recompute participants, location, type, status from attached evidence."""
        rows = self.s.execute(
            select(EventCluster.id, EventCluster.country_iso3s, EventCluster.action_country_iso3, EventCluster.quad_class,
                   EventCluster.event_root_code, EventCluster.member_count, EventCluster.latitude, EventCluster.longitude,
                   EventCluster.source_domains, EventCluster.last_seen_at, EventCluster.verification_status,
                   EventCluster.max_severity)
            .join(CrisisEvent, CrisisEvent.event_cluster_id == EventCluster.id)
            .where(CrisisEvent.crisis_id == crisis.id)
        ).all()
        if not rows:
            return
        country_counts: Counter[str] = Counter()
        loc_counts: Counter[str] = Counter()
        lat_sum = lon_sum = w_sum = 0.0
        domains: set[str] = set()
        multi = 0
        views = []
        for r in rows:
            country_counts.update(r[1] or [])
            if r[2]:
                loc_counts[r[2]] += 1
            if r[6] is not None and r[7] is not None:
                w = math.log1p(r[5] or 1)
                lat_sum += r[6] * w
                lon_sum += r[7] * w
                w_sum += w
            domains.update(r[8] or [])
            multi += r[10] == VERIFICATION_MULTI_SOURCE
            views.append(ClusterView(r[0], set(r[1] or []), r[2], r[4], r[3], r[11] or 0, r[5] or 0, set(r[8] or []), r[9],
                                     r[6], r[7], r[10] == VERIFICATION_MULTI_SOURCE))
        min_ev = self.settings.crisis_participant_min_events
        existing = {p.participant_key: p for p in self.s.scalars(
            select(CrisisParticipant).where(CrisisParticipant.crisis_id == crisis.id))}
        cid_by_iso = dict(self.s.execute(select(Country.iso3, Country.id).where(Country.iso3.in_(list(country_counts)))).all())
        core = set(crisis.core_country_iso3s or [])
        added = []
        for iso, n in country_counts.items():
            key = f"country:{iso}"
            role = "location" if loc_counts.get(iso, 0) >= min_ev else ("core" if iso in core else "involved")
            if iso in core and role != "location":
                role = "core"
            p = existing.get(key)
            if p is None:
                if n < min_ev and iso not in core:
                    continue
                p = CrisisParticipant(crisis_id=crisis.id, participant_key=key, country_id=cid_by_iso.get(iso), role=role,
                                      first_seen_at=self.now, last_seen_at=self.now, event_count=n, active=True)
                self.s.add(p)
                if iso not in core:
                    added.append(iso)
            else:
                p.event_count = n
                p.role = role
                p.last_seen_at = self.now
        if added:
            _log(self.s, crisis, "participant_added",
                 f"New participants observed in ≥{min_ev} attached event clusters: "
                 f"{', '.join(self.names.get(x, x) for x in added)}.",
                 {"countries": added, "counts": {x: country_counts[x] for x in added}})
            crisis.meta = {**(crisis.meta or {}), "last_participant_added_at": self.now.isoformat()}
        self._refresh_actor_participants(crisis)
        # locations grow with evidence (multi-country theaters)
        locs = {iso for iso, n in loc_counts.items() if n >= max(min_ev, 0.15 * len(rows))}
        crisis.location_country_iso3s = sorted(set(crisis.location_country_iso3s or []) | locs)
        if w_sum:
            crisis.latitude, crisis.longitude = lat_sum / w_sum, lon_sum / w_sum
        crisis.event_cluster_count = len(rows)
        crisis.crisis_type = _crisis_type(views)
        crisis.verification_status = VERIFICATION_MULTI_SOURCE if multi >= max(3, 0.3 * len(rows)) else "machine_extracted"
        crisis.last_event_at = max(r[9] for r in rows)
        if crisis.title_source == "rule":
            crisis.title = self._title(core | set(crisis.location_country_iso3s), set(crisis.location_country_iso3s), views)
        # lifecycle
        idle = self.now - crisis.last_event_at
        if crisis.status in ("candidate", "active") and idle > timedelta(days=self.settings.crisis_dormant_after_days):
            crisis.status = "dormant"
            _log(self.s, crisis, "status_changed", f"No new evidence for {idle.days} days; marked dormant.", {})
        elif crisis.status == "candidate" and (len(rows) >= 2 * self.min_clusters and len(domains) >= 2 * self.min_sources):
            crisis.status = "active"
            _log(self.s, crisis, "status_changed",
                 f"Promoted to active: {len(rows)} event clusters from {len(domains)} independent outlets.",
                 {"event_clusters": len(rows), "independent_sources": len(domains)})

    def _refresh_actor_participants(self, crisis: Crisis) -> None:
        rows = self.s.execute(text(
            "SELECT a.id, a.actor_key, count(*) FROM crisis_events ce "
            "JOIN normalized_geopolitical_events e ON e.cluster_id = ce.event_cluster_id "
            "JOIN actors a ON a.id IN (e.actor1_id, e.actor2_id) "
            "WHERE ce.crisis_id = :cid AND a.actor_type = ANY(:types) "
            "GROUP BY a.id, a.actor_key ORDER BY count(*) DESC LIMIT 15"
        ), {"cid": crisis.id, "types": sorted(POLITICAL_ACTOR_TYPES | {"country"})}).all()
        for aid, key, n in rows:
            if n < self.settings.crisis_participant_min_events:
                continue
            stmt = insert(CrisisParticipant).values(
                crisis_id=crisis.id, participant_key=f"actor:{key}", actor_id=aid, role="actor",
                first_seen_at=self.now, last_seen_at=self.now, event_count=n, active=True,
            ).on_conflict_do_update(index_elements=["crisis_id", "participant_key"],
                                    set_={"event_count": n, "last_seen_at": self.now})
            self.s.execute(stmt)

    # -------------------------------------------------------------- articles
    def link_articles(self) -> int:
        since = self.now - timedelta(hours=48)
        crises = list(self.s.scalars(select(Crisis).where(Crisis.status.in_(["candidate", "active"]))))
        linked = 0
        for c in crises:
            core = sorted(set(c.core_country_iso3s or []) | set(c.location_country_iso3s or []))
            if not core:
                continue
            need = 2 if len(core) >= 2 else 1
            q = select(NewsArticle.id, NewsArticle.country_iso3s).where(
                NewsArticle.seen_at >= since, NewsArticle.country_iso3s.overlap(core)
            ).limit(2000)
            rows = [
                {"crisis_id": c.id, "article_id": aid, "added_at": self.now,
                 "match_reason": "countries:" + ",".join(sorted(set(isos) & set(core)))}
                for aid, isos in self.s.execute(q).all() if len(set(isos) & set(core)) >= need
            ]
            if rows:
                res = self.s.execute(insert(CrisisArticle).values(rows).on_conflict_do_nothing()
                                     .returning(CrisisArticle.article_id))
                linked += len(res.all())
        return linked


def _crisis_type(views: list[ClusterView]) -> str:
    if not views:
        return "mixed"
    n = len(views)
    violent = sum(1 for v in views if v.root in VIOLENT_ROOTS)
    diplomatic = sum(1 for v in views if v.root in DIPLOMATIC_ROOTS)
    protest = sum(1 for v in views if v.root == "14")
    if violent / n >= 0.3:
        return "armed_conflict"
    if protest / n >= 0.4:
        return "unrest"
    if diplomatic / n >= 0.4:
        return "diplomatic"
    return "mixed"


def region_name(session: Session, region_id: int | None) -> str | None:
    if region_id is None:
        return None
    r = session.get(Region, region_id)
    return r.name if r else None
