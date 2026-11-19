"""UCDP Georeferenced Event Dataset (GED) connector — versioned historical imports.

UCDP is a human-coded research dataset with documented definitions, so it is
used for historical analysis and *independently defined* ML outcome labels.
Each release is imported as its own ``dataset_versions`` row (with citation
and checksum); records of different versions never overwrite each other.

The bulk CSV downloads are used (the UCDP API requires an access token).
``available_at`` of every record is the release's publication time, so
point-in-time feature construction never sees a release before it existed.
"""

from __future__ import annotations

import csv
import hashlib
import io
import zipfile
from datetime import date, datetime, timezone
from email.utils import parsedate_to_datetime
from pathlib import Path

import httpx
from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.orm import Session

from app.core.config import get_settings
from app.core.db import utcnow
from app.core.logging import get_logger
from app.geopolitics.models import UcdpEvent
from app.ingestion import http
from app.ingestion.models import DatasetVersion
from app.ingestion.runs import ingestion_run
from app.ingestion.sources import UCDP_CITATION, get_source
from app.registry.service import RegistryResolver

log = get_logger(__name__)
SOURCE_KEY = "ucdp_ged"
DROP_RAW = {"source_article", "source_original", "geom_wkt"}
BATCH = 2000  # 27 columns x 2000 rows stays under the 65535 bind-parameter limit


def ged_url(version: str) -> str:
    return f"{get_settings().ucdp_downloads_url}/ged/ged{version.replace('.', '')}-csv.zip"


def _date(v: str) -> date:
    return datetime.strptime(v[:10], "%Y-%m-%d").date()


def _int(v: str | None) -> int:
    try:
        return int(float(v)) if v not in (None, "") else 0
    except ValueError:
        return 0


def _float(v: str | None) -> float | None:
    try:
        return float(v) if v not in (None, "") else None
    except ValueError:
        return None


class UcdpGedConnector:
    def __init__(self, session: Session):
        self.session = session
        self.settings = get_settings()
        self.source = get_source(session, SOURCE_KEY)
        self.resolver = RegistryResolver.load(session)

    def _download(self, version: str) -> tuple[bytes, datetime | None, str]:
        url = ged_url(version)
        resp = http.fetch(url)
        released = None
        if resp.headers.get("last-modified"):
            try:
                released = parsedate_to_datetime(resp.headers["last-modified"])
            except (TypeError, ValueError):
                released = None
        return resp.content, released, url

    def _release_date(self, version: str, path: Path) -> datetime:
        """Publication time of a release imported from a local file: the official file's
        Last-Modified header when reachable, else the local file's modification time."""
        try:
            with httpx.Client(timeout=20, follow_redirects=True) as c:
                lm = c.head(ged_url(version)).headers.get("last-modified")
            if lm:
                return parsedate_to_datetime(lm)
        except (httpx.HTTPError, TypeError, ValueError):
            pass
        return datetime.fromtimestamp(path.stat().st_mtime, tz=timezone.utc)

    def _rows(self, payload: bytes, name: str):
        if name.endswith(".zip") or payload[:2] == b"PK":
            with zipfile.ZipFile(io.BytesIO(payload)) as zf:
                csv_name = next(n for n in zf.namelist() if n.lower().endswith(".csv"))
                text = zf.read(csv_name).decode("utf-8")
        else:
            text = payload.decode("utf-8")
        yield from csv.DictReader(io.StringIO(text))

    def import_version(self, version: str | None = None, min_year: int | None = None, path: str | None = None,
                       released_at: datetime | None = None) -> dict:
        version = version or self.settings.ucdp_ged_version
        min_year = min_year if min_year is not None else self.settings.ucdp_min_year
        with ingestion_run(self.session, self.source.id, "ucdp_ged.import", version=version) as run:
            if path:
                payload = Path(path).read_bytes()
                url, released = f"file://{path}", released_at or self._release_date(version, Path(path))
            else:
                payload, released, url = self._download(version)
            released = released or released_at or utcnow()
            checksum = hashlib.sha256(payload).hexdigest()
            dv = self.session.scalar(select(DatasetVersion).where(DatasetVersion.data_source_id == self.source.id,
                                                                  DatasetVersion.version == version))
            if dv is None:
                dv = DatasetVersion(data_source_id=self.source.id, version=version, released_at=released,
                                    citation=f"UCDP GED version {version}. {UCDP_CITATION}", source_url=url,
                                    checksum=checksum, meta={"min_year": min_year})
                self.session.add(dv)
                self.session.flush()
            elif dv.checksum and dv.checksum != checksum:
                dv.meta = {**(dv.meta or {}), "checksum_changed_at": utcnow().isoformat(), "new_checksum": checksum}
            fetched = inserted = 0
            buf: list[dict] = []
            for r in self._rows(payload, url):
                fetched += 1
                if _int(r.get("year")) < min_year:
                    continue
                buf.append(self._map(r, dv.id, released))
                if len(buf) >= BATCH:
                    inserted += self._flush(buf)
                    buf = []
            if buf:
                inserted += self._flush(buf)
            dv.record_count = (dv.record_count or 0) + inserted
            run.records_fetched, run.records_inserted = fetched, inserted
            run.records_skipped = fetched - inserted
            self.session.commit()
            log.info("ucdp_import_done", version=version, fetched=fetched, inserted=inserted)
            return {"version": version, "dataset_version_id": dv.id, "fetched": fetched, "inserted": inserted,
                    "released_at": released.isoformat()}

    def _map(self, r: dict, dv_id: int, released: datetime) -> dict:
        gw = (r.get("country_id") or "").split(",")[0].strip()
        cid = self.resolver.gw_country.get(gw) or self.resolver.country_from_name(r.get("country"))
        tov = _int(r.get("type_of_violence"))
        interstate = tov == 1 and bool((r.get("gwnoa") or "").strip()) and bool((r.get("gwnob") or "").strip())
        return dict(
            dataset_version_id=dv_id, ucdp_id=r["id"], conflict_id=r.get("conflict_new_id") or None,
            conflict_name=(r.get("conflict_name") or "")[:300] or None, dyad_id=r.get("dyad_new_id") or None,
            dyad_name=(r.get("dyad_name") or "")[:300] or None, type_of_violence=tov,
            side_a=(r.get("side_a") or "")[:300] or None, side_b=(r.get("side_b") or "")[:300] or None,
            side_a_gw=r.get("gwnoa") or None, side_b_gw=r.get("gwnob") or None, country_id=cid,
            country_name=r.get("country") or None, region=r.get("region") or None,
            date_start=_date(r["date_start"]), date_end=_date(r["date_end"]),
            latitude=_float(r.get("latitude")), longitude=_float(r.get("longitude")),
            where_description=(r.get("where_description") or None), deaths_best=_int(r.get("best")),
            deaths_low=_int(r.get("low")), deaths_high=_int(r.get("high")),
            event_clarity=_int(r.get("event_clarity")) or None, date_precision=_int(r.get("date_prec")) or None,
            is_interstate=interstate, available_at=released,
            raw={k: v for k, v in r.items() if k not in DROP_RAW},
        )

    def _flush(self, rows: list[dict]) -> int:
        res = self.session.execute(insert(UcdpEvent).values(rows)
                                   .on_conflict_do_nothing(index_elements=["dataset_version_id", "ucdp_id"])
                                   .returning(UcdpEvent.id))
        n = len(res.all())
        self.session.commit()
        return n

    def check_and_import_configured(self) -> dict:
        """Scheduled: import the configured release if not yet present (UCDP publishes ~annually)."""
        v = self.settings.ucdp_ged_version
        exists = self.session.scalar(select(DatasetVersion).where(DatasetVersion.data_source_id == self.source.id,
                                                                  DatasetVersion.version == v))
        if exists and exists.record_count:
            return {"version": v, "status": "already_imported"}
        return self.import_version(v)


def latest_version(session: Session) -> DatasetVersion | None:
    src = get_source(session, SOURCE_KEY)
    return session.scalar(select(DatasetVersion).where(DatasetVersion.data_source_id == src.id)
                          .order_by(DatasetVersion.released_at.desc().nulls_last()).limit(1))


def as_utc(d: date) -> datetime:
    return datetime(d.year, d.month, d.day, tzinfo=timezone.utc)
