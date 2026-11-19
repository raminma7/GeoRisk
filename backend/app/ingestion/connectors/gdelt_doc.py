"""GDELT DOC 2.0 API connector — news discovery.

The DOC API is a search interface over GDELT's monitored news, *not* an
exhaustive firehose of world news. Only article metadata (headline, URL,
domain, language, source country, timestamp) is stored; full text is never
fetched or stored, preserving publisher copyright.

Each query (topic queries + watchlist countries) is its own checkpointed
stream, so a failed query is retried from its own last successful time.
GDELT asks clients to keep to <= 1 request / 5 seconds; this is enforced.
"""

from __future__ import annotations

import json
from collections.abc import Callable
from datetime import datetime, timedelta

from sqlalchemy import select, text
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.orm import Session

from app.core.config import get_settings
from app.core.db import utcnow
from app.core.logging import get_logger
from app.core.runtime_settings import get_value
from app.geopolitics.models import GeopoliticalIndicator, NewsArticle
from app.geopolitics.news import cluster_new_articles, match_countries, normalize_title, parse_seendate
from app.geopolitics.normalize import domain_of, url_hash
from app.ingestion import http
from app.ingestion.runs import get_checkpoint, ingestion_run, set_checkpoint
from app.ingestion.sources import get_source
from app.registry.models import Country
from app.registry.service import RegistryResolver

log = get_logger(__name__)
SOURCE_KEY = "gdelt_doc"

DocFetcher = Callable[[dict], dict]


def default_doc_fetcher(params: dict) -> dict:
    s = get_settings()
    resp = http.fetch(s.gdelt_doc_url, params=params, min_interval=s.gdelt_doc_min_interval_seconds)
    text = resp.text.strip()
    if not text or text == "{}":
        return {"articles": []}
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        # GDELT occasionally emits invalid escapes; tolerate them
        return json.loads(text.encode("utf-8").decode("unicode_escape", errors="ignore"))


class GdeltDocConnector:
    def __init__(self, session: Session, fetcher: DocFetcher | None = None):
        self.session = session
        self.fetch = fetcher or default_doc_fetcher
        self.settings = get_settings()
        self.source = get_source(session, SOURCE_KEY)
        self.resolver = RegistryResolver.load(session)

    def queries(self) -> list[tuple[str, str, str | None, int | None]]:
        """(stream_key, query, topic, country_id) for every configured query."""
        out: list[tuple[str, str, str | None, int | None]] = []
        for topic, q in get_value(self.session, "gdelt_doc_topic_queries").items():
            out.append((f"topic:{topic}", q, topic, None))
        watch = get_value(self.session, "watchlist")
        for iso3 in watch:
            c = self.session.scalar(select(Country).where(Country.iso3 == iso3))
            if c:
                out.append((f"country:{iso3}", f'"{c.name}" {self.settings.gdelt_doc_country_terms}', None, c.id))
        return out

    def run(self, max_queries: int | None = None, now: datetime | None = None) -> dict:
        now = now or utcnow()
        stats = {"queries": 0, "failed_queries": 0, "fetched": 0, "inserted": 0, "clusters_created": 0}
        with ingestion_run(self.session, self.source.id, "gdelt_doc.run") as run:
            new_ids: list[int] = []
            for stream, query, topic, country_id in self.queries()[: max_queries or None]:
                try:
                    fetched, ids = self.run_query(stream, query, topic, country_id, now)
                    stats["queries"] += 1
                    stats["fetched"] += fetched
                    stats["inserted"] += len(ids)
                    new_ids.extend(ids)
                    self.session.commit()
                except Exception as exc:  # noqa: BLE001 - one failing query must not stop discovery
                    self.session.rollback()
                    stats["failed_queries"] += 1
                    log.warning("gdelt_doc_query_failed", stream=stream, error=f"{type(exc).__name__}: {exc}")
            stats["clusters_created"] = cluster_new_articles(self.session, new_ids)
            run = self.session.merge(run)
            run.records_fetched = stats["fetched"]
            run.records_inserted = stats["inserted"]
            run.window_end = now
            run.meta = stats
            if stats["failed_queries"]:
                run.status = "partial" if stats["queries"] else "failed"
                if not stats["queries"]:
                    run.error = "all DOC queries failed (rate limiting or upstream unavailable)"
            self.session.commit()
        return stats

    def run_query(
        self, stream: str, query: str, topic: str | None, country_id: int | None, now: datetime
    ) -> tuple[int, list[int]]:
        # The DOC index can lag real time by many hours, so the checkpoint is the
        # newest *seendate* observed for this stream (minus an overlap), not "now".
        cp = get_checkpoint(self.session, self.source.id, stream)
        max_window = timedelta(hours=self.settings.gdelt_doc_lookback_hours)
        start = cp.marker_time - timedelta(minutes=30) if cp and cp.marker_time else now - max_window
        start = max(start, now - max_window)
        params = {
            "query": query,
            "mode": "artlist",
            "format": "json",
            "maxrecords": str(self.settings.gdelt_doc_max_records),
            "sort": "datedesc",
            "startdatetime": start.strftime("%Y%m%d%H%M%S"),
            "enddatetime": now.strftime("%Y%m%d%H%M%S"),
        }
        data = self.fetch(params)
        articles = data.get("articles") or []
        ids = self.store_articles(articles, stream, topic, country_id)
        # media-coverage tracking (coverage volume is recorded, never used alone as escalation)
        self.session.execute(
            insert(GeopoliticalIndicator)
            .values(
                scope_type="news_query", scope_key=stream, indicator="article_count", window="run",
                as_of=now, value=float(len(articles)), algorithm_version="doc-v1",
                meta={"window_start": start.isoformat(), "truncated": len(articles) >= self.settings.gdelt_doc_max_records},
            )
            .on_conflict_do_nothing()
        )
        newest = max((parse_seendate(a["seendate"]) for a in articles if a.get("seendate")), default=None)
        if newest:
            set_checkpoint(self.session, self.source.id, newest.strftime("%Y%m%d%H%M%S"), newest, stream=stream)
        return len(articles), ids

    def store_articles(
        self, articles: list[dict], stream: str, topic: str | None, country_id: int | None
    ) -> list[int]:
        rows = []
        seen: set[str] = set()
        for a in articles:
            url = (a.get("url") or "").strip()
            title = (a.get("title") or "").strip()
            if not url or not title:
                continue
            h = url_hash(url)
            if h in seen:
                continue
            seen.add(h)
            try:
                seen_at = parse_seendate(a["seendate"])
            except (KeyError, ValueError):
                continue
            countries = match_countries(title, self.resolver)
            if country_id:
                countries.add(country_id)
            iso3s = sorted({self.resolver.iso3(c) for c in countries if self.resolver.iso3(c)})
            src_country = self.resolver.country_from_name(a.get("sourcecountry"))
            rows.append({
                "url": url, "url_hash": h, "title": title[:2000], "title_norm": normalize_title(title)[:2000],
                "domain": a.get("domain") or domain_of(url), "language": a.get("language") or None,
                "source_country_id": src_country, "source_country_name": a.get("sourcecountry") or None,
                "seen_at": seen_at, "ingested_at": utcnow(), "data_source_id": self.source.id,
                "query_keys": [stream], "topic": topic, "country_iso3s": iso3s, "meta": {},
            })
        if not rows:
            return []
        stmt = insert(NewsArticle).values(rows)
        stmt = stmt.on_conflict_do_update(
            index_elements=["url_hash"],
            set_={
                # correlated to the conflicting row only (no FROM clause)
                "query_keys": text("ARRAY(SELECT DISTINCT unnest(news_articles.query_keys || excluded.query_keys))"),
                "country_iso3s": text("ARRAY(SELECT DISTINCT unnest(news_articles.country_iso3s || excluded.country_iso3s))"),
            },
        ).returning(NewsArticle.id, NewsArticle.cluster_id)
        result = self.session.execute(stmt).all()
        return [aid for aid, cluster in result if cluster is None]
