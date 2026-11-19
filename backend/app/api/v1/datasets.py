"""Datasets & Models: historical exports, training datasets, model evaluations."""

from __future__ import annotations

from datetime import date
from typing import Literal

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import FileResponse
from pydantic import BaseModel
from sqlalchemy import desc, func, select, text
from sqlalchemy.orm import Session

from app.api.v1.common import default_range, iso
from app.core.config import get_settings
from app.core.db import get_db
from app.core.ratelimit import limiter
from app.core.security import Principal, require_read, require_role
from app.ingestion.models import AuditLog, DatasetVersion, DataSource
from app.ml.export import EXPORTS, ExportNotPermitted, export_dataset
from app.ml.models import FeatureSnapshot, ModelEvaluation, ModelVersion, OutcomeLabel, TrainingDataset

router = APIRouter(tags=["Datasets & Models"])


@router.get("/datasets", summary="Historical data inventory, dataset versions, exports and training datasets")
def datasets(s: Session = Depends(get_db), _: Principal = Depends(require_read)):
    counts = {
        "raw_geopolitical_events": s.scalar(text("SELECT reltuples::bigint FROM pg_class WHERE relname='raw_geopolitical_events'")),
        "normalized_geopolitical_events": s.scalar(text("SELECT count(*) FROM normalized_geopolitical_events")),
        "event_clusters": s.scalar(text("SELECT count(*) FROM event_clusters")),
        "news_articles": s.scalar(text("SELECT count(*) FROM news_articles")),
        "ucdp_events": s.scalar(text("SELECT count(*) FROM ucdp_events")),
        "feature_snapshots": s.scalar(select(func.count()).select_from(FeatureSnapshot)),
        "outcome_labels": s.scalar(select(func.count()).select_from(OutcomeLabel)),
        "crisis_snapshots": s.scalar(text("SELECT count(*) FROM crisis_snapshots")),
        "monitoring_priorities": s.scalar(text("SELECT count(*) FROM monitoring_priorities")),
    }
    versions = s.execute(select(DatasetVersion, DataSource.key).join(DataSource, DataSource.id == DatasetVersion.data_source_id)
                         .order_by(desc(DatasetVersion.imported_at))).all()
    tds = s.scalars(select(TrainingDataset).order_by(desc(TrainingDataset.created_at)).limit(50))
    snaps = s.execute(select(FeatureSnapshot.feature_set_version, FeatureSnapshot.entity_type, func.count(),
                             func.min(FeatureSnapshot.as_of), func.max(FeatureSnapshot.as_of))
                      .group_by(FeatureSnapshot.feature_set_version, FeatureSnapshot.entity_type)).all()
    return {
        "record_counts": counts,
        "dataset_versions": [{"source": k, "version": v.version, "released_at": iso(v.released_at),
                              "imported_at": iso(v.imported_at), "records": v.record_count, "citation": v.citation,
                              "checksum": v.checksum} for v, k in versions],
        "feature_snapshots": [{"feature_set_version": f, "entity_type": e, "count": n, "first_as_of": iso(a),
                               "last_as_of": iso(b)} for f, e, n, a, b in snaps],
        "training_datasets": [{"id": t.id, "name": t.name, "version": t.version, "rows": t.row_count, "format": t.file_format,
                               "checksum": t.checksum, "definition": t.definition, "licensing": t.licensing,
                               "created_at": iso(t.created_at), "download": f"/api/v1/datasets/{t.id}/download"
                               if t.file_path else None} for t in tds],
        "export_kinds": {k: {"sources": v["sources"], "ml": v.get("ml", False)} for k, v in EXPORTS.items()},
    }


class ExportRequest(BaseModel):
    kind: str
    format: Literal["csv", "parquet"] = "parquet"
    start: date | None = None
    end: date | None = None


@router.post("/datasets/export", summary="Create a licensing-checked CSV/Parquet export (analyst)")
@limiter.limit(get_settings().rate_limit_expensive)
def export(request: Request, body: ExportRequest, s: Session = Depends(get_db), p: Principal = Depends(require_role("analyst"))):
    if body.kind not in EXPORTS:
        raise HTTPException(422, f"kind must be one of {sorted(EXPORTS)}")
    st, en = default_range(body.start, body.end, days=30)
    try:
        ds = export_dataset(s, body.kind, body.format, st, en, p.name)
    except ExportNotPermitted as exc:
        raise HTTPException(403, f"Export refused by licensing controls: {exc}") from exc
    s.add(AuditLog(actor=p.name, action="dataset_export", target=body.kind, details=body.model_dump(mode="json")))
    s.commit()
    return {"id": ds.id, "rows": ds.row_count, "format": ds.file_format, "checksum": ds.checksum,
            "licensing": ds.licensing, "download": f"/api/v1/datasets/{ds.id}/download"}


@router.get("/datasets/{dataset_id}/download")
def download(dataset_id: int, s: Session = Depends(get_db), _: Principal = Depends(require_role("analyst"))):
    ds = s.get(TrainingDataset, dataset_id)
    if ds is None or not ds.file_path:
        raise HTTPException(404, "Dataset file not found")
    return FileResponse(ds.file_path, filename=ds.file_path.rsplit("/", 1)[-1])


@router.get("/models", summary="Model versions and out-of-time evaluations (experimental)")
def models(s: Session = Depends(get_db), _: Principal = Depends(require_read)):
    out = []
    for m in s.scalars(select(ModelVersion).order_by(desc(ModelVersion.created_at)).limit(20)):
        evs = s.scalars(select(ModelEvaluation).where(ModelEvaluation.model_version_id == m.id).order_by(ModelEvaluation.fold))
        out.append({"id": m.id, "name": m.name, "version": m.version, "algorithm": m.algorithm, "status": m.status,
                    "training_dataset_id": m.training_dataset_id, "random_seed": m.random_seed, "notes": m.notes,
                    "created_at": iso(m.created_at),
                    "evaluations": [{"fold": e.fold, "split": e.split_kind, "train": [iso(e.train_start), iso(e.train_end)],
                                     "test": [iso(e.test_start), iso(e.test_end)], "metrics": e.metrics,
                                     "baselines": e.baseline_metrics, "notes": e.notes} for e in evs]})
    return {"items": out, "disclaimer": "Experimental research models. Scores are not validated probabilities of war "
                                        "and are not used by the monitoring priority engine."}
