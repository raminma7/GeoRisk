"""Historical news signals per country-month from the GDELT 1.0 Events archive.

The archive (``data.gdeltproject.org/events``) has yearly files for 1979–2005, monthly
files for 2006–Mar 2013 and daily files from Apr 2013. Each file is streamed,
aggregated to (country, month) counts and discarded; only aggregates are kept in
``news_country_month_parts`` (one row set per file, so re-runs are idempotent).

A country is counted for an event if it is the location (FIPS) or the affiliation
of either actor (CAMEO). ``WLD`` rows count every event once, for normalization:
GDELT's volume grew by orders of magnitude over time, so models should use
*shares* of world coverage rather than raw counts.

Events are dated by ``SQLDATE``. Daily files also re-report old events; rows more
than 45 days older than the file date are dropped to keep months comparable.
"""

from __future__ import annotations

import os
import re
import tempfile
import zipfile
from concurrent.futures import ProcessPoolExecutor, as_completed
from datetime import date, datetime

import numpy as np
import pandas as pd
from sqlalchemy import delete, select, text
from sqlalchemy.dialects.postgresql import insert

from app.core.db import SessionLocal, utcnow
from app.core.logging import get_logger
from app.geopolitics.cameo import DE_ESCALATION_PREFIXES
from app.geopolitics.models import NewsCountryMonthPart
from app.ingestion.models import IngestionBatch
from app.ingestion.runs import register_batches
from app.ingestion.sources import get_source
from app.registry.service import RegistryResolver

log = get_logger(__name__)
SOURCE_KEY = "gdelt_v1_history"
BASE = "https://data.gdeltproject.org/events"
# 0-based column positions in GDELT 1.0 event files (57 cols; daily files add SOURCEURL)
COLS = {1: "sqldate", 7: "a1", 17: "a2", 26: "code", 28: "root", 29: "quad", 30: "goldstein", 32: "nsrc",
        33: "nart", 34: "tone", 51: "geo"}
METRICS = ["events", "material_conflict", "verbal_conflict", "verbal_cooperation", "material_cooperation", "violent",
           "threats", "protests", "diplomatic_tension", "de_escalation", "goldstein_sum", "tone_sum", "num_sources",
           "num_articles"]


def list_files(start_year: int, end_year: int) -> list[tuple[str, date]]:
    """(file name, nominal date) for every archive file in range, from the archive index."""
    from app.ingestion import http

    html = http.fetch(f"{BASE}/index.html").text
    out = []
    for name in sorted(set(re.findall(r'href="([0-9]{4}(?:[0-9]{2}){0,2}(?:\.export\.CSV)?\.zip)"', html, re.IGNORECASE))):
        stem = name.split(".")[0]
        y = int(stem[:4])
        if not (start_year <= y <= end_year):
            continue
        d = date(y, int(stem[4:6]) if len(stem) >= 6 else 1, int(stem[6:8]) if len(stem) == 8 else 1)
        out.append((name, d))
    return out


def _maps() -> tuple[dict[str, str], dict[str, str]]:
    with SessionLocal() as s:
        r = RegistryResolver.load(s)
    cameo = {k: r.iso3_by_id[v] for k, v in r.cameo_country.items() if v in r.iso3_by_id}
    fips = {k: r.iso3_by_id[v] for k, v in r.fips_country.items() if v in r.iso3_by_id}
    return cameo, fips


def aggregate_file(path: str, file_date: date, cameo: dict[str, str], fips: dict[str, str], daily: bool) -> pd.DataFrame:
    """Aggregate one archive file into (iso3, month) metric rows (plus WLD totals)."""
    parts = []
    with zipfile.ZipFile(path) as zf:
        name = zf.namelist()[0]
        reader = pd.read_csv(zf.open(name), sep="\t", header=None, usecols=list(COLS), names=None, dtype=str,
                             quoting=3, chunksize=1_500_000, on_bad_lines="skip", engine="c")
        for chunk in reader:
            chunk = chunk.rename(columns=COLS)
            d = pd.to_datetime(chunk["sqldate"], format="%Y%m%d", errors="coerce")
            ok = d.notna()
            if daily:
                ok &= d >= pd.Timestamp(file_date) - pd.Timedelta(days=45)
            chunk, d = chunk[ok], d[ok]
            month = d.dt.to_period("M").dt.to_timestamp()
            root = chunk["root"].fillna("").str.zfill(2)
            code = chunk["code"].fillna("")
            quad = pd.to_numeric(chunk["quad"], errors="coerce")
            m = pd.DataFrame({
                "month": month.values,
                "events": 1,
                "material_conflict": (quad == 4).astype(int).values,
                "verbal_conflict": (quad == 3).astype(int).values,
                "verbal_cooperation": (quad == 1).astype(int).values,
                "material_cooperation": (quad == 2).astype(int).values,
                "violent": root.isin(["18", "19", "20"]).astype(int).values,
                "threats": (root == "13").astype(int).values,
                "protests": (root == "14").astype(int).values,
                "diplomatic_tension": root.isin(["10", "11", "12", "16"]).astype(int).values,
                "de_escalation": code.str.startswith(DE_ESCALATION_PREFIXES).astype(int).values,
                "goldstein_sum": pd.to_numeric(chunk["goldstein"], errors="coerce").fillna(0).values,
                "tone_sum": pd.to_numeric(chunk["tone"], errors="coerce").fillna(0).values,
                "num_sources": pd.to_numeric(chunk["nsrc"], errors="coerce").fillna(0).astype(np.int64).values,
                "num_articles": pd.to_numeric(chunk["nart"], errors="coerce").fillna(0).astype(np.int64).values,
            })
            rid = np.arange(len(m))
            a1 = chunk["a1"].map(cameo).values
            a2 = chunk["a2"].map(cameo).values
            g = chunk["geo"].map(fips).values
            links = pd.DataFrame({"rid": np.concatenate([rid, rid, rid]), "iso3": np.concatenate([a1, a2, g])})
            links = links.dropna().drop_duplicates()
            per_c = m.iloc[links["rid"].values].assign(iso3=links["iso3"].values)
            parts.append(per_c.groupby(["iso3", "month"], as_index=False)[METRICS].sum())
            parts.append(m.assign(iso3="WLD").groupby(["iso3", "month"], as_index=False)[METRICS].sum())
    if not parts:
        return pd.DataFrame(columns=["iso3", "month", *METRICS])
    return pd.concat(parts).groupby(["iso3", "month"], as_index=False)[METRICS].sum()


def _work(name: str, file_date: date, cameo: dict, fips: dict) -> tuple[str, pd.DataFrame | None, str | None]:
    from app.ingestion import http

    tmp = tempfile.NamedTemporaryFile(suffix=".zip", delete=False, dir=os.environ.get("NEWS_TMP_DIR"))
    try:
        tmp.write(http.fetch(f"{BASE}/{name}").content)
        tmp.close()
        return name, aggregate_file(tmp.name, file_date, cameo, fips, daily=len(name.split(".")[0]) == 8), None
    except Exception as exc:  # noqa: BLE001 - recorded per file, retried next run
        return name, None, f"{type(exc).__name__}: {exc}"[:1000]
    finally:
        try:
            os.unlink(tmp.name)
        except OSError:
            pass


def _store(file_key: str, df: pd.DataFrame) -> int:
    with SessionLocal() as s:
        s.execute(delete(NewsCountryMonthPart).where(NewsCountryMonthPart.file_key == file_key))
        rows = [{"file_key": file_key, "iso3": r.iso3, "month": r.month.date(),
                 **{k: (float(getattr(r, k)) if k.endswith("_sum") else int(getattr(r, k))) for k in METRICS}}
                for r in df.itertuples(index=False)]
        for i in range(0, len(rows), 2000):
            s.execute(insert(NewsCountryMonthPart).values(rows[i:i + 2000]))
        src = get_source(s, SOURCE_KEY)
        b = s.scalar(select(IngestionBatch).where(IngestionBatch.data_source_id == src.id, IngestionBatch.batch_key == file_key))
        b.status, b.records, b.processed_at, b.last_error = "done", len(rows), utcnow(), None
        b.attempts += 1
        s.commit()
    return len(rows)


def backfill(start_year: int = 1988, end_year: int = 2025, workers: int = 4, max_files: int | None = None,
             on_progress=None) -> dict:
    """``on_progress(done, total)`` is called after each file (used for setup status in the UI)."""
    files = list_files(start_year, end_year)
    with SessionLocal() as s:
        src = get_source(s, SOURCE_KEY)
        register_batches(s, src.id, [(n, datetime.combine(d, datetime.min.time()), f"{BASE}/{n}") for n, d in files])
        s.commit()
        done = set(s.scalars(select(IngestionBatch.batch_key).where(IngestionBatch.data_source_id == src.id,
                                                                    IngestionBatch.status == "done")))
    todo = [(n, d) for n, d in files if n not in done][: max_files or None]
    cameo, fips = _maps()
    stats = {"files_total": len(files), "already_done": len(done), "processed": 0, "failed": 0, "rows": 0}
    log.info("news_history_start", todo=len(todo))
    import multiprocessing

    # Celery prefork workers are daemonic and may not spawn child processes: process files in-line there.
    if workers <= 1 or multiprocessing.current_process().daemon:
        results = (_work(n, d, cameo, fips) for n, d in todo)
    else:
        ex = ProcessPoolExecutor(max_workers=workers)
        results = (f.result() for f in as_completed([ex.submit(_work, n, d, cameo, fips) for n, d in todo]))
    for name, df, err in results:
        if err:
            stats["failed"] += 1
            with SessionLocal() as s:
                src = get_source(s, SOURCE_KEY)
                b = s.scalar(select(IngestionBatch).where(IngestionBatch.data_source_id == src.id,
                                                          IngestionBatch.batch_key == name))
                b.status, b.last_error = "failed", err
                b.attempts += 1
                s.commit()
            log.warning("news_history_file_failed", file=name, error=err)
            continue
        stats["rows"] += _store(name, df)
        stats["processed"] += 1
        if on_progress:
            on_progress(stats["already_done"] + stats["processed"], stats["files_total"])
        if stats["processed"] % 50 == 0:
            log.info("news_history_progress", **stats)
    return stats


def monthly_frame(session) -> pd.DataFrame:
    """Country-month news aggregates (summed over files) with world totals joined."""
    q = text("SELECT iso3, month, " + ", ".join(f"sum({m}) AS {m}" for m in METRICS) +
             " FROM news_country_month_parts GROUP BY iso3, month")
    df = pd.read_sql(q, session.connection())
    if df.empty:
        return df
    df["month"] = pd.to_datetime(df["month"])
    world = df[df.iso3 == "WLD"][["month", "events"]].rename(columns={"events": "world_events"})
    return df[df.iso3 != "WLD"].merge(world, on="month", how="left")
