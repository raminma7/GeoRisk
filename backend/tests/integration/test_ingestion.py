"""GDELT ingestion: idempotency, checkpoints, gap recovery, backfill; DOC discovery."""

import json
from datetime import datetime, timedelta, timezone

from sqlalchemy import func, select

from app.geopolitics.models import (
    EventCluster,
    EventCountryRole,
    GeopoliticalIndicator,
    NewsArticle,
    NormalizedGeopoliticalEvent,
    RawGeopoliticalEvent,
)
from app.ingestion.connectors.gdelt_doc import GdeltDocConnector
from app.ingestion.connectors.gdelt_events import GdeltEventsConnector, read_export_zip
from app.ingestion.models import IngestionBatch, IngestionCheckpoint, IngestionRun
from tests.conftest import FIXTURES


def count(db, model):
    return db.scalar(select(func.count()).select_from(model))


def test_first_run_imports_real_fixture_batches(db, fetcher):
    stats = GdeltEventsConnector(db, fetcher=fetcher).run()
    assert stats["planned"] == 4  # initial lookback: 09:00, 09:15, 09:30, 09:45
    assert stats["inserted"] == 550  # 300 + 250 rows in the two fixture files
    assert count(db, RawGeopoliticalEvent) == 550
    assert count(db, NormalizedGeopoliticalEvent) == 550
    assert count(db, EventCountryRole) > 0
    assert count(db, EventCluster) > 0
    statuses = dict(db.execute(select(IngestionBatch.batch_key, IngestionBatch.status)).all())
    assert statuses["20261119094500"] == "done" and statuses["20261119093000"] == "done"
    assert statuses["20261119090000"] == "failed"  # not in fixtures -> 404, retried later
    run = db.scalar(select(IngestionRun).order_by(IngestionRun.id.desc()))
    assert run.status in ("success", "partial") and run.records_inserted == 550


def test_restart_does_not_duplicate(db, fetcher):
    c = GdeltEventsConnector(db, fetcher=fetcher)
    c.run()
    before = (count(db, RawGeopoliticalEvent), count(db, NormalizedGeopoliticalEvent), count(db, EventCountryRole))
    # simulate a crash/restart that re-processes an already ingested batch
    db.execute(IngestionBatch.__table__.update().values(status="pending"))
    db.commit()
    c2 = GdeltEventsConnector(db, fetcher=fetcher)
    c2.run()
    rows = read_export_zip((FIXTURES / "20261119093000.export.CSV.zip").read_bytes())
    assert c2.store_rows(rows, "manual") == 0
    after = (count(db, RawGeopoliticalEvent), count(db, NormalizedGeopoliticalEvent), count(db, EventCountryRole))
    assert before == after
    # cluster membership counts were not double-incremented
    assert db.scalar(select(func.sum(EventCluster.member_count))) == db.scalar(
        select(func.count()).where(NormalizedGeopoliticalEvent.cluster_id.is_not(None)))


def test_missed_intervals_are_registered_and_recovered(db, fetcher):
    c = GdeltEventsConnector(db, fetcher=fetcher)
    src = c.source.id
    # checkpoint says we last saw 08:00 -> 07 slots missing before the 09:45 file
    db.add(IngestionCheckpoint(data_source_id=src, stream="default", marker="20261119080000",
                               marker_time=datetime(2026, 11, 19, 8, 0, tzinfo=timezone.utc)))
    db.commit()
    stats = c.run()
    assert stats["planned"] == 7  # 08:15 ... 09:45
    keys = set(db.scalars(select(IngestionBatch.batch_key)))
    assert {"20261119081500", "20261119093000", "20261119094500"} <= keys
    # unavailable slots become missing_upstream after 3 attempts and stop being retried
    for _ in range(3):
        c.run()
    missing = db.scalar(select(func.count()).where(IngestionBatch.status == "missing_upstream"))
    assert missing == 5
    assert db.scalar(select(func.count()).where(IngestionBatch.status == "done")) == 2


def test_backfill_range(db, fetcher):
    c = GdeltEventsConnector(db, fetcher=fetcher)
    n = c.plan_range(datetime(2026, 11, 19, 9, 30, tzinfo=timezone.utc), datetime(2026, 11, 19, 9, 45, tzinfo=timezone.utc))
    assert n == 2
    stats = c.process_pending(10)
    assert stats["inserted"] == 550
    assert db.scalar(select(IngestionCheckpoint)) is None  # backfill never moves the live checkpoint


def test_events_preserve_gdelt_identifiers_and_raw(ingested):
    raw = ingested.scalar(select(RawGeopoliticalEvent).limit(1))
    ev = ingested.scalar(select(NormalizedGeopoliticalEvent).where(NormalizedGeopoliticalEvent.raw_event_id == raw.id))
    assert raw.raw["GLOBALEVENTID"] == ev.source_record_id
    assert ev.verification_status in ("machine_extracted", "multi_source_reported")
    assert ev.available_at.tzinfo is not None


def _doc_fetcher(payload):
    calls = []

    def f(params):
        calls.append(params)
        return payload

    f.calls = calls
    return f


def test_doc_discovery_dedup_and_coverage(db):
    payload = json.loads((FIXTURES / "gdelt_doc_artlist.json").read_text())
    f = _doc_fetcher(payload)
    now = datetime(2026, 11, 19, 10, 0, tzinfo=timezone.utc)
    c = GdeltDocConnector(db, fetcher=f)
    stats = c.run(max_queries=2, now=now)
    assert stats["queries"] == 2
    unique_urls = len({a["url"] for a in payload["articles"]})
    assert count(db, NewsArticle) == unique_urls
    arts = db.scalars(select(NewsArticle)).all()
    assert all(len(a.query_keys) == 2 for a in arts)  # matched by both queries, stored once
    assert all(a.cluster_id is not None for a in arts)
    dup = [a for a in arts if a.title.startswith("Albanese")]
    if len(dup) == 2:  # same headline from two URLs -> one deduplicated news cluster
        assert dup[0].cluster_id == dup[1].cluster_id
    assert db.scalar(select(func.count()).where(GeopoliticalIndicator.scope_type == "news_query")) == 2
    # second run: overlapping window, no duplicates; checkpoint anchored to newest seendate
    c.run(max_queries=2, now=now + timedelta(minutes=15))
    assert count(db, NewsArticle) == unique_urls
    cp = db.scalar(select(IngestionCheckpoint).where(IngestionCheckpoint.stream == "topic:armed_conflict"))
    assert cp.marker_time < now
    assert f.calls[-1]["startdatetime"] < now.strftime("%Y%m%d%H%M%S")
    # multilingual sources preserved
    assert len(set(db.scalars(select(NewsArticle.language)))) > 3


def test_doc_failure_is_recorded_not_raised(db):
    def boom(params):
        raise RuntimeError("upstream down")

    stats = GdeltDocConnector(db, fetcher=boom).run(max_queries=1)
    assert stats["failed_queries"] == 1
    run = db.scalar(select(IngestionRun).order_by(IngestionRun.id.desc()))
    assert run.status == "failed" and "failed" in (run.error or "")


def test_doc_stops_early_when_rate_limited(db):
    import httpx

    calls = []

    def limited(params):
        calls.append(params)
        req = httpx.Request("GET", "https://api.gdeltproject.org/api/v2/doc/doc")
        raise httpx.HTTPStatusError("429", request=req, response=httpx.Response(429, request=req))

    stats = GdeltDocConnector(db, fetcher=limited).run()
    assert stats["stopped_early"] == "rate_limited"
    assert len(calls) == 2  # did not keep hammering the remaining queries
