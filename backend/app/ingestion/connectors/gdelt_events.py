"""GDELT Events 2.0 connector.

Workflow per run:

1. Read ``lastupdate.txt`` to learn the newest published 15-minute file.
2. Enumerate every expected 15-minute slot between the checkpoint and the
   newest file and register them in ``ingestion_batches`` (pending). Missed
   intervals therefore stay visible until recovered.
3. Process pending/failed batches oldest-first (bounded per run): download,
   parse, insert raw rows with ``ON CONFLICT DO NOTHING`` (restarts never
   duplicate), normalize only newly inserted rows, upsert dedup clusters.

GDELT records are machine-extracted from news and are *not* treated as
independently verified facts.
"""

from __future__ import annotations

import csv
import hashlib
import io
import zipfile
from collections import defaultdict
from collections.abc import Callable
from datetime import datetime, timedelta, timezone

from sqlalchemy import func, select, text, update
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.orm import Session

from app.core.config import get_settings
from app.core.db import utcnow
from app.core.logging import get_logger
from app.geopolitics.models import (
    VERIFICATION_MULTI_SOURCE,
    EventCluster,
    EventCountryRole,
    NormalizedGeopoliticalEvent,
    RawGeopoliticalEvent,
)
from app.geopolitics.normalize import (
    domain_of,
    normalize_gdelt,
    parse_gdelt_ts,
    parse_row,
    url_hash,
)
from app.ingestion import http
from app.ingestion.models import IngestionBatch, SourceDocument
from app.ingestion.runs import get_checkpoint, ingestion_run, register_batches, set_checkpoint
from app.ingestion.sources import get_source
from app.registry.service import RegistryResolver

log = get_logger(__name__)

SOURCE_KEY = "gdelt_events"
SLOT = timedelta(minutes=15)
MULTI_SOURCE_MIN_DOMAINS = 3
CHUNK = 1000

Fetcher = Callable[[str], bytes]


def _https(url: str) -> str:
    # GDELT publishes http:// links; the same files are served over https.
    return "https://" + url[len("http://"):] if url.startswith("http://") else url


def slot_key(t: datetime) -> str:
    return t.strftime("%Y%m%d%H%M%S")


def slot_url(t: datetime) -> str:
    return f"{get_settings().gdelt_base_url}/{slot_key(t)}.export.CSV.zip"


def floor_slot(t: datetime) -> datetime:
    t = t.astimezone(timezone.utc).replace(second=0, microsecond=0)
    return t - timedelta(minutes=t.minute % 15)


def parse_lastupdate(text_: str) -> tuple[datetime, str, str]:
    """Return (slot_time, url, md5) of the newest *events export* file."""
    for line in text_.strip().splitlines():
        parts = line.split()
        if len(parts) == 3 and parts[2].endswith(".export.CSV.zip"):
            name = parts[2].rsplit("/", 1)[-1]
            return parse_gdelt_ts(name.split(".")[0]), _https(parts[2]), parts[1]
    raise ValueError("lastupdate.txt did not list an events export file")


def default_fetcher(url: str) -> bytes:
    return http.fetch_bytes(_https(url))


def read_export_zip(payload: bytes) -> list[dict[str, str]]:
    with zipfile.ZipFile(io.BytesIO(payload)) as zf:
        name = zf.namelist()[0]
        data = zf.read(name).decode("utf-8", errors="replace")
    rows = []
    for fields in csv.reader(io.StringIO(data), delimiter="\t", quoting=csv.QUOTE_NONE):
        row = parse_row(fields)
        if row and row["GLOBALEVENTID"]:
            rows.append(row)
    return rows


class GdeltEventsConnector:
    def __init__(self, session: Session, fetcher: Fetcher | None = None):
        self.session = session
        self.fetch = fetcher or default_fetcher
        self.settings = get_settings()
        self.source = get_source(session, SOURCE_KEY)
        self._resolver: RegistryResolver | None = None

    @property
    def resolver(self) -> RegistryResolver:
        if self._resolver is None:
            self._resolver = RegistryResolver.load(self.session)
        return self._resolver

    # ------------------------------------------------------------------ planning
    def latest_available(self) -> tuple[datetime, str, str]:
        payload = self.fetch(f"{self.settings.gdelt_base_url}/lastupdate.txt")
        return parse_lastupdate(payload.decode("utf-8", errors="replace"))

    def plan(self, latest: datetime) -> int:
        """Register expected batches up to ``latest``; returns number newly registered."""
        cp = get_checkpoint(self.session, self.source.id)
        if cp and cp.marker_time:
            start = cp.marker_time + SLOT
        else:
            start = latest - SLOT * (self.settings.gdelt_initial_lookback_batches - 1)
        slots = []
        t = start
        while t <= latest:
            slots.append((slot_key(t), t, slot_url(t)))
            t += SLOT
        n = register_batches(self.session, self.source.id, slots)
        if slots:
            set_checkpoint(self.session, self.source.id, slots[-1][0], latest)
        self.session.commit()
        return n

    def plan_range(self, start: datetime, end: datetime) -> int:
        """Backfill: register every slot in [start, end] (does not move the live checkpoint)."""
        t = floor_slot(start)
        slots = []
        while t <= end:
            slots.append((slot_key(t), t, slot_url(t)))
            t += SLOT
        n = register_batches(self.session, self.source.id, slots)
        self.session.commit()
        return n

    # ---------------------------------------------------------------- execution
    def pending_batches(self, limit: int, max_attempts: int = 5) -> list[IngestionBatch]:
        return list(
            self.session.scalars(
                select(IngestionBatch)
                .where(
                    IngestionBatch.data_source_id == self.source.id,
                    IngestionBatch.status.in_(["pending", "failed"]),
                    IngestionBatch.attempts < max_attempts,
                )
                .order_by(IngestionBatch.batch_time.desc())  # newest first: freshness matters most
                .limit(limit)
            )
        )

    def run(self, max_batches: int | None = None) -> dict:
        """Scheduled entry point: plan from lastupdate.txt, then recover/process."""
        max_batches = max_batches or self.settings.gdelt_max_recovery_batches
        with ingestion_run(self.session, self.source.id, "gdelt_events.run") as run:
            latest, _, _ = self.latest_available()
            planned = self.plan(latest)
            stats = self.process_pending(max_batches, run_id=run.id)
            run.window_end = latest
            run.records_fetched = stats["fetched"]
            run.records_inserted = stats["inserted"]
            run.records_skipped = stats["skipped"]
            run.meta = {**(run.meta or {}), **stats, "planned": planned}
            if stats["failed"]:
                run.status = "partial"
            self.session.commit()
            return {**stats, "planned": planned, "latest": latest.isoformat()}

    def process_pending(self, max_batches: int, run_id: int | None = None) -> dict:
        stats = {"batches": 0, "fetched": 0, "inserted": 0, "skipped": 0, "failed": 0, "missing": 0}
        for batch in self.pending_batches(max_batches):
            try:
                r = self.process_batch(batch, run_id=run_id)
                stats["batches"] += 1
                stats["fetched"] += r["fetched"]
                stats["inserted"] += r["inserted"]
                stats["skipped"] += r["fetched"] - r["inserted"]
            except http.NotFoundError:
                self.session.rollback()
                batch = self.session.merge(batch)
                batch.attempts += 1
                # GDELT occasionally never publishes a slot; stop retrying after 3 tries.
                batch.status = "missing_upstream" if batch.attempts >= 3 else "failed"
                batch.last_error = "404 Not Found"
                stats["missing"] += 1
                self.session.commit()
            except Exception as exc:  # noqa: BLE001 - recorded per batch, retried next run
                self.session.rollback()
                batch = self.session.merge(batch)
                batch.attempts += 1
                batch.status = "failed"
                batch.last_error = f"{type(exc).__name__}: {exc}"[:2000]
                stats["failed"] += 1
                self.session.commit()
                log.warning("gdelt_batch_failed", batch=batch.batch_key, error=batch.last_error)
        return stats

    def process_batch(self, batch: IngestionBatch, run_id: int | None = None) -> dict:
        payload = self.fetch(batch.source_url or slot_url(batch.batch_time))
        rows = read_export_zip(payload)
        inserted = self.store_rows(rows, batch.batch_key)
        batch.status = "done"
        batch.attempts += 1
        batch.records = len(rows)
        batch.inserted = inserted
        batch.checksum = hashlib.md5(payload).hexdigest()  # noqa: S324 - integrity, matches GDELT md5
        batch.processed_at = utcnow()
        batch.last_error = None
        batch.run_id = run_id
        self.session.commit()
        log.info("gdelt_batch_done", batch=batch.batch_key, rows=len(rows), inserted=inserted)
        return {"fetched": len(rows), "inserted": inserted}

    # ------------------------------------------------------------------ storage
    def store_rows(self, rows: list[dict[str, str]], batch_key: str) -> int:
        total = 0
        for i in range(0, len(rows), CHUNK):
            total += self._store_chunk(rows[i : i + CHUNK], batch_key)
        return total

    def _store_chunk(self, rows: list[dict[str, str]], batch_key: str) -> int:
        s = self.session
        src_id = self.source.id
        # 1) raw, idempotent
        by_id = {r["GLOBALEVENTID"]: r for r in rows}
        values = []
        for rid, r in by_id.items():
            try:
                avail = parse_gdelt_ts(r["DATEADDED"])
            except ValueError:
                avail = None
            values.append(
                {"data_source_id": src_id, "source_record_id": rid, "batch_key": batch_key,
                 "available_at": avail, "raw": r, "ingested_at": utcnow()}
            )
        if not values:
            return 0
        res = s.execute(
            insert(RawGeopoliticalEvent)
            .values(values)
            .on_conflict_do_nothing(index_elements=["data_source_id", "source_record_id"])
            .returning(RawGeopoliticalEvent.id, RawGeopoliticalEvent.source_record_id)
        )
        new_raw = {rid: raw_id for raw_id, rid in res.all()}
        if not new_raw:
            return 0

        # 2) normalize new rows
        normed = []
        for rid, raw_id in new_raw.items():
            ne = normalize_gdelt(by_id[rid], self.resolver)
            if ne is not None:
                normed.append((raw_id, ne))

        # 3) source documents (URL metadata only)
        urls = {ne.source_url for _, ne in normed if ne.source_url}
        doc_ids: dict[str, int] = {}
        if urls:
            s.execute(
                insert(SourceDocument)
                .values([{"url": u, "url_hash": url_hash(u), "domain": domain_of(u), "data_source_id": src_id,
                          "first_seen_at": utcnow(), "meta": {}} for u in urls])
                .on_conflict_do_nothing(index_elements=["url_hash"])
            )
            hashes = {url_hash(u): u for u in urls}
            for did, h in s.execute(select(SourceDocument.id, SourceDocument.url_hash).where(SourceDocument.url_hash.in_(hashes))):
                doc_ids[hashes[h]] = did

        # 4) dedup clusters
        agg: dict[str, dict] = {}
        for _, ne in normed:
            if not ne.cluster_key:
                continue
            v = ne.values
            a = agg.setdefault(ne.cluster_key, {
                "cluster_key": ne.cluster_key, "kind": "event", "title": ne.cluster_title,
                "event_date": v["event_date"], "event_root_code": v["event_root_code"], "quad_class": v["quad_class"],
                "country_iso3s": ne.country_iso3s,
                "action_country_iso3": self.resolver.iso3(v["action_country_id"]),
                "first_seen_at": v["available_at"], "last_seen_at": v["available_at"], "member_count": 0,
                "domains": set(), "max_severity": 0.0, "latitude": v["latitude"], "longitude": v["longitude"],
                "meta": {},
            })
            a["member_count"] += 1
            if v["source_domain"]:
                a["domains"].add(v["source_domain"])
            a["max_severity"] = max(a["max_severity"], v["severity"])
            a["first_seen_at"] = min(a["first_seen_at"], v["available_at"])
            a["last_seen_at"] = max(a["last_seen_at"], v["available_at"])
            if a["latitude"] is None and v["latitude"] is not None:
                a["latitude"], a["longitude"] = v["latitude"], v["longitude"]
        cluster_ids: dict[str, int] = {}
        if agg:
            rows_ = []
            for a in agg.values():
                a = dict(a)
                a["source_domains"] = sorted(a.pop("domains"))[:50]
                rows_.append(a)
            stmt = insert(EventCluster).values(rows_)
            ex = stmt.excluded
            stmt = stmt.on_conflict_do_update(
                index_elements=["cluster_key"],
                set_={
                    "member_count": EventCluster.member_count + ex.member_count,
                    "last_seen_at": func.greatest(EventCluster.last_seen_at, ex.last_seen_at),
                    "max_severity": func.greatest(EventCluster.max_severity, ex.max_severity),
                    "source_domains": text(
                        "(SELECT coalesce(array_agg(DISTINCT d), '{}') FROM "
                        "(SELECT unnest(event_clusters.source_domains || excluded.source_domains) AS d LIMIT 200) q)"
                    ),
                },
            ).returning(EventCluster.id, EventCluster.cluster_key)
            for cid, key in s.execute(stmt).all():
                cluster_ids[key] = cid
            s.execute(
                update(EventCluster)
                .where(EventCluster.id.in_(cluster_ids.values()))
                .where(func.cardinality(EventCluster.source_domains) >= MULTI_SOURCE_MIN_DOMAINS)
                .values(verification_status=VERIFICATION_MULTI_SOURCE)
            )

        # 5) normalized events
        ev_values = []
        role_rows: list[tuple[int, list[tuple[int, str]]]] = []
        for raw_id, ne in normed:
            v = dict(ne.values)
            v["raw_event_id"] = raw_id
            v["data_source_id"] = src_id
            v["actor1_id"] = self.resolver.ensure_actor(s, ne.actor1) if ne.actor1 else None
            v["actor2_id"] = self.resolver.ensure_actor(s, ne.actor2) if ne.actor2 else None
            v["source_document_id"] = doc_ids.get(ne.source_url) if ne.source_url else None
            v["cluster_id"] = cluster_ids.get(ne.cluster_key) if ne.cluster_key else None
            v["ingested_at"] = utcnow()
            ev_values.append(v)
            role_rows.append((raw_id, ne.roles))
        if ev_values:
            res = s.execute(
                insert(NormalizedGeopoliticalEvent)
                .values(ev_values)
                .on_conflict_do_nothing(
                    index_elements=["data_source_id", "source_record_id", "normalization_version"]
                )
                .returning(NormalizedGeopoliticalEvent.id, NormalizedGeopoliticalEvent.raw_event_id)
            )
            ev_by_raw = {raw: eid for eid, raw in res.all()}
            roles = [
                {"event_id": ev_by_raw[raw], "country_id": cid, "role": role}
                for raw, rs in role_rows if raw in ev_by_raw for cid, role in rs
            ]
            if roles:
                s.execute(insert(EventCountryRole).values(roles).on_conflict_do_nothing())
            # cluster representative = most-sourced member (cheap heuristic)
            if cluster_ids:
                s.execute(text(
                    "UPDATE event_clusters c SET representative_event_id = sub.id FROM ("
                    " SELECT DISTINCT ON (cluster_id) cluster_id, id FROM normalized_geopolitical_events"
                    " WHERE cluster_id = ANY(:ids) ORDER BY cluster_id, num_sources DESC, num_mentions DESC, id) sub"
                    " WHERE c.id = sub.cluster_id"
                ), {"ids": list(cluster_ids.values())})
        s.flush()
        return len(new_raw)


def gap_report(session: Session) -> dict:
    """Summarize batch states (used by monitoring status)."""
    src = get_source(session, SOURCE_KEY)
    rows = session.execute(
        select(IngestionBatch.status, func.count()).where(IngestionBatch.data_source_id == src.id).group_by(IngestionBatch.status)
    ).all()
    counts = defaultdict(int, {k: v for k, v in rows})
    return dict(counts)
