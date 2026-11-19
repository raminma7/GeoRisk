"""News article helpers: title normalization, country mention matching and
near-duplicate clustering (deduplicated news clusters)."""

from __future__ import annotations

import hashlib
import re
import unicodedata
from datetime import datetime, timedelta

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.db import utcnow
from app.geopolitics.models import EventCluster, NewsArticle
from app.registry.service import RegistryResolver

CLUSTER_SIMILARITY = 0.6
CLUSTER_WINDOW_HOURS = 48
_STOP = {
    "the", "a", "an", "of", "to", "in", "on", "and", "or", "for", "with", "at", "by", "from", "as", "is", "are",
    "was", "be", "after", "over", "its", "it", "that", "this", "says", "said", "new", "de", "la", "le", "el",
    "en", "y", "di", "il", "der", "die", "und", "des", "les", "du", "na", "za", "u", "i", "v", "s", "o",
}


def normalize_title(title: str) -> str:
    t = unicodedata.normalize("NFKC", title or "").lower()
    t = re.sub(r"[\W_]+", " ", t, flags=re.UNICODE)
    return re.sub(r"\s+", " ", t).strip()


def tokens(title_norm: str) -> set[str]:
    return {w for w in title_norm.split() if len(w) > 1 and w not in _STOP}


def jaccard(a: set[str], b: set[str]) -> float:
    if not a or not b:
        return 0.0
    return len(a & b) / len(a | b)


def _ascii(s: str) -> str:
    return unicodedata.normalize("NFKD", s).encode("ascii", "ignore").decode().lower()


def match_countries(title: str, resolver: RegistryResolver, max_ngram: int = 4) -> set[int]:
    """Countries whose (>=4 character) alias appears in the title as a whole word sequence."""
    words = re.sub(r"[^a-z0-9]+", " ", _ascii(title)).split()
    found: set[int] = set()
    for n in range(1, max_ngram + 1):
        for i in range(len(words) - n + 1):
            gram = " ".join(words[i : i + n])
            if len(gram) < 4:
                continue
            cid = resolver.alias_country.get(gram)
            if cid:
                found.add(cid)
    return found


def cluster_new_articles(session: Session, article_ids: list[int]) -> int:
    """Assign each article to an existing recent news cluster or a new one."""
    if not article_ids:
        return 0
    since = utcnow() - timedelta(hours=CLUSTER_WINDOW_HOURS)
    clusters = session.execute(
        select(EventCluster.id, EventCluster.title).where(EventCluster.kind == "news", EventCluster.last_seen_at >= since)
    ).all()
    index: dict[str, set[int]] = {}
    toks: dict[int, set[str]] = {}
    for cid, title in clusters:
        tk = tokens(normalize_title(title or ""))
        toks[cid] = tk
        for w in tk:
            index.setdefault(w, set()).add(cid)

    created = 0
    arts = session.scalars(select(NewsArticle).where(NewsArticle.id.in_(article_ids)).order_by(NewsArticle.seen_at))
    for art in arts:
        tk = tokens(art.title_norm)
        cand: dict[int, int] = {}
        for w in tk:
            for cid in index.get(w, ()):
                cand[cid] = cand.get(cid, 0) + 1
        best, best_sim = None, 0.0
        for cid, shared in cand.items():
            if shared < 2:
                continue
            sim = jaccard(tk, toks[cid])
            if sim > best_sim:
                best, best_sim = cid, sim
        if best is not None and best_sim >= CLUSTER_SIMILARITY:
            cl = session.get(EventCluster, best)
            assert cl is not None
            cl.member_count += 1
            cl.last_seen_at = max(cl.last_seen_at, art.seen_at)
            if art.domain and art.domain not in (cl.source_domains or []):
                cl.source_domains = [*(cl.source_domains or []), art.domain][:200]
            cl.country_iso3s = sorted(set(cl.country_iso3s or []) | set(art.country_iso3s or []))
            art.cluster_id = cl.id
        else:
            key = "news:" + hashlib.sha1(f"{art.title_norm}|{art.seen_at.date()}".encode()).hexdigest()  # noqa: S324
            cl = session.scalar(select(EventCluster).where(EventCluster.cluster_key == key))
            if cl is None:
                cl = EventCluster(
                    cluster_key=key, kind="news", title=art.title, event_date=art.seen_at.date(),
                    country_iso3s=art.country_iso3s or [], first_seen_at=art.seen_at, last_seen_at=art.seen_at,
                    member_count=0, source_domains=[], meta={"language": art.language},
                )
                session.add(cl)
                session.flush()
                created += 1
            cl.member_count += 1
            if art.domain and art.domain not in cl.source_domains:
                cl.source_domains = [*cl.source_domains, art.domain]
            art.cluster_id = cl.id
            toks[cl.id] = tk
            for w in tk:
                index.setdefault(w, set()).add(cl.id)
    session.flush()
    return created


def parse_seendate(v: str) -> datetime:
    from datetime import timezone

    return datetime.strptime(v, "%Y%m%dT%H%M%SZ").replace(tzinfo=timezone.utc)
