"""Licensing-aware CSV/Parquet dataset exports.

Each export kind declares the data sources it draws on. The export is refused
unless every source permits export (``source_permissions.allow_export``);
ML training frames additionally require ``allow_ml_training``. ACLED is never
included. News exports contain metadata and links only.
"""

from __future__ import annotations

import hashlib
import json
from datetime import datetime
from pathlib import Path

import pandas as pd
from sqlalchemy import select, text
from sqlalchemy.orm import Session

from app.core.config import get_settings
from app.core.db import utcnow
from app.ingestion.models import DataSource, SourcePermission
from app.ml.models import TrainingDataset


class ExportNotPermitted(Exception):
    pass


EXPORTS: dict[str, dict] = {
    "events": {"sources": ["gdelt_events"], "sql": (
        "SELECT e.id, e.source_record_id AS gdelt_globaleventid, e.event_date, e.available_at, e.ingested_at, "
        " e.event_code, e.event_root_code, e.category, e.quad_class, e.goldstein, e.severity, e.signal_type, "
        " e.num_mentions, e.num_sources, e.num_articles, e.avg_tone, e.actor1_name, e.actor2_name, "
        " c1.iso3 AS actor1_country, c2.iso3 AS actor2_country, ca.iso3 AS location_country, e.action_location, "
        " e.latitude, e.longitude, e.source_url, e.cluster_id, e.verification_status, e.normalization_version "
        "FROM normalized_geopolitical_events e LEFT JOIN countries c1 ON c1.id=e.actor1_country_id "
        "LEFT JOIN countries c2 ON c2.id=e.actor2_country_id LEFT JOIN countries ca ON ca.id=e.action_country_id "
        "WHERE e.available_at BETWEEN :start AND :end ORDER BY e.available_at")},
    "event_clusters": {"sources": ["gdelt_events"], "sql": (
        "SELECT id, cluster_key, kind, title, event_date, event_root_code, quad_class, country_iso3s, "
        " action_country_iso3, first_seen_at, last_seen_at, member_count, cardinality(source_domains) AS independent_sources, "
        " max_severity, latitude, longitude, verification_status FROM event_clusters "
        "WHERE kind='event' AND last_seen_at BETWEEN :start AND :end ORDER BY first_seen_at")},
    "crises": {"sources": ["gdelt_events"], "sql": (
        "SELECT id, uid, title, status, crisis_type, core_country_iso3s, location_country_iso3s, first_observed_at, "
        " last_event_at, current_priority, priority_level, direction, verification_status, needs_review, "
        " event_cluster_count, algorithm_version FROM crises WHERE last_event_at >= :start AND first_observed_at <= :end")},
    "monitoring_priorities": {"sources": ["gdelt_events"], "sql": (
        "SELECT id, crisis_id, score, level, direction, components, independent_source_count, evidence_confidence, "
        " algorithm_version, computed_at FROM monitoring_priorities WHERE computed_at BETWEEN :start AND :end")},
    "news_articles": {"sources": ["gdelt_doc"], "sql": (
        "SELECT id, url, domain, title, language, source_country_name, seen_at, topic, country_iso3s, cluster_id "
        "FROM news_articles WHERE seen_at BETWEEN :start AND :end ORDER BY seen_at")},
    "feature_snapshots": {"sources": ["gdelt_events", "ucdp_ged"], "ml": True, "sql": (
        "SELECT entity_type, entity_key, as_of, feature_set_version, features, source_vintages FROM feature_snapshots "
        "WHERE as_of BETWEEN :start AND :end ORDER BY as_of")},
    "outcome_labels": {"sources": ["ucdp_ged"], "ml": True, "sql": (
        "SELECT entity_type, entity_key, label_name, label_version, window_start, window_end, value, "
        " source_dataset_version_id FROM outcome_labels WHERE window_start BETWEEN :start AND :end")},
    "ucdp_events": {"sources": ["ucdp_ged"], "sql": (
        "SELECT u.ucdp_id, dv.version AS ucdp_version, u.conflict_id, u.conflict_name, u.dyad_name, u.type_of_violence, "
        " u.side_a, u.side_b, c.iso3, u.country_name, u.date_start, u.date_end, u.latitude, u.longitude, "
        " u.deaths_best, u.deaths_low, u.deaths_high, u.is_interstate FROM ucdp_events u "
        "JOIN dataset_versions dv ON dv.id = u.dataset_version_id LEFT JOIN countries c ON c.id = u.country_id "
        "WHERE u.date_start BETWEEN :start AND :end ORDER BY u.date_start")},
}


def check_permissions(session: Session, sources: list[str], ml: bool = False) -> dict:
    rows = session.execute(select(DataSource.key, SourcePermission, DataSource.citation, DataSource.license)
                           .join(SourcePermission, SourcePermission.data_source_id == DataSource.id)
                           .where(DataSource.key.in_(sources))).all()
    found = {k: (p, cit, lic) for k, p, cit, lic in rows}
    for s in sources:
        if s not in found:
            raise ExportNotPermitted(f"source '{s}' has no permission record")
        p = found[s][0]
        if not p.allow_export:
            raise ExportNotPermitted(f"source '{s}' does not permit export")
        if ml and not p.allow_ml_training:
            raise ExportNotPermitted(f"source '{s}' does not permit ML training use")
    return {s: {"license": found[s][2], "citation": found[s][1], "redistribution_allowed": found[s][0].allow_redistribution}
            for s in sources}


def export_dataset(session: Session, kind: str, fmt: str, start: datetime, end: datetime, requested_by: str) -> TrainingDataset:
    if kind not in EXPORTS:
        raise KeyError(kind)
    spec = EXPORTS[kind]
    licensing = check_permissions(session, spec["sources"], ml=spec.get("ml", False))
    df = pd.read_sql(text(spec["sql"]), session.connection(), params={"start": start, "end": end})
    for col in df.columns:  # JSON/array columns -> strings for CSV portability
        if df[col].dtype == object and len(df) and isinstance(df[col].iloc[0], (dict, list)):
            df[col] = df[col].map(lambda v: json.dumps(v, default=str) if v is not None else None)
    out_dir = Path(get_settings().export_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    stamp = utcnow().strftime("%Y%m%dT%H%M%S")
    path = out_dir / f"{kind}_{start:%Y%m%d}_{end:%Y%m%d}_{stamp}.{fmt}"
    if fmt == "parquet":
        df.to_parquet(path, index=False)
    else:
        df.to_csv(path, index=False)
    checksum = hashlib.sha256(path.read_bytes()).hexdigest()
    ds = TrainingDataset(
        name=f"export:{kind}", version=f"{stamp}-{checksum[:8]}", row_count=len(df), file_path=str(path), file_format=fmt,
        checksum=checksum, definition={"kind": kind, "start": start.isoformat(), "end": end.isoformat(),
                                       "requested_by": requested_by, "columns": list(df.columns)},
        licensing={"sources": licensing, "notice": "Cite all sources. GDELT-derived records are machine-extracted and "
                                                    "unverified. News exports include metadata/links only."},
    )
    session.add(ds)
    session.flush()
    return ds
