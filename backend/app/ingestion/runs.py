"""Helpers for ingestion runs, checkpoints and batch tracking."""

from __future__ import annotations

from contextlib import contextmanager
from datetime import datetime

from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.orm import Session

from app.core.db import utcnow
from app.core.logging import get_logger
from app.ingestion.models import IngestionBatch, IngestionCheckpoint, IngestionRun

log = get_logger(__name__)


@contextmanager
def ingestion_run(session: Session, data_source_id: int, task: str, **meta):
    run = IngestionRun(data_source_id=data_source_id, task=task, status="running", meta=meta)
    session.add(run)
    session.commit()
    try:
        yield run
        if run.status == "running":
            run.status = "success"
    except Exception as exc:  # recorded, then re-raised for Celery retry semantics
        session.rollback()
        run = session.merge(run)
        run.status = "failed"
        run.error = f"{type(exc).__name__}: {exc}"[:4000]
        log.error("ingestion_run_failed", task=task, error=run.error)
        raise
    finally:
        run.finished_at = utcnow()
        session.commit()


def get_checkpoint(session: Session, data_source_id: int, stream: str = "default") -> IngestionCheckpoint | None:
    return session.scalar(
        select(IngestionCheckpoint).where(
            IngestionCheckpoint.data_source_id == data_source_id, IngestionCheckpoint.stream == stream
        )
    )


def set_checkpoint(
    session: Session, data_source_id: int, marker: str, marker_time: datetime | None, stream: str = "default"
) -> None:
    stmt = insert(IngestionCheckpoint).values(
        data_source_id=data_source_id, stream=stream, marker=marker, marker_time=marker_time, updated_at=utcnow()
    )
    stmt = stmt.on_conflict_do_update(
        index_elements=["data_source_id", "stream"],
        set_={"marker": marker, "marker_time": marker_time, "updated_at": utcnow()},
    )
    session.execute(stmt)


def register_batches(session: Session, data_source_id: int, batches: list[tuple[str, datetime, str]]) -> int:
    """Insert expected batches (key, time, url) as pending; existing ones untouched."""
    if not batches:
        return 0
    stmt = insert(IngestionBatch).values(
        [
            {"data_source_id": data_source_id, "batch_key": k, "batch_time": t, "source_url": u, "status": "pending"}
            for k, t, u in batches
        ]
    ).on_conflict_do_nothing(index_elements=["data_source_id", "batch_key"]).returning(IngestionBatch.id)
    return len(session.execute(stmt).all())
