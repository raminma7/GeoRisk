"""Provenance and ingestion bookkeeping shared by all connectors."""

from __future__ import annotations

from datetime import datetime

from sqlalchemy import Boolean, ForeignKey, Index, Integer, String, Text, UniqueConstraint
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.core.db import Base, utcnow


class DataSource(Base):
    __tablename__ = "data_sources"
    id: Mapped[int] = mapped_column(primary_key=True)
    key: Mapped[str] = mapped_column(String(60), unique=True)  # gdelt_events | gdelt_doc | ucdp_ged | acled | ...
    name: Mapped[str] = mapped_column(String(200))
    module: Mapped[str] = mapped_column(String(40))  # geopolitics | economy | shared
    kind: Mapped[str] = mapped_column(String(40))  # event_stream | news_search | dataset | indicator_api
    homepage_url: Mapped[str | None] = mapped_column(String(500))
    license: Mapped[str | None] = mapped_column(Text)
    citation: Mapped[str | None] = mapped_column(Text)
    enabled: Mapped[bool] = mapped_column(Boolean, default=True)
    expected_interval_minutes: Mapped[int | None] = mapped_column(Integer)
    config: Mapped[dict] = mapped_column(JSONB, default=dict)

    permission: Mapped["SourcePermission | None"] = relationship(back_populates="data_source", uselist=False)


class SourcePermission(Base):
    """Licensing controls checked by AI processing, ML training and exports."""

    __tablename__ = "source_permissions"
    id: Mapped[int] = mapped_column(primary_key=True)
    data_source_id: Mapped[int] = mapped_column(ForeignKey("data_sources.id", ondelete="CASCADE"), unique=True)
    allow_storage: Mapped[bool] = mapped_column(Boolean, default=True)
    allow_ai_processing: Mapped[bool] = mapped_column(Boolean, default=True)
    allow_ml_training: Mapped[bool] = mapped_column(Boolean, default=True)
    allow_export: Mapped[bool] = mapped_column(Boolean, default=True)
    allow_redistribution: Mapped[bool] = mapped_column(Boolean, default=False)
    store_full_text: Mapped[bool] = mapped_column(Boolean, default=False)
    notes: Mapped[str | None] = mapped_column(Text)

    data_source: Mapped[DataSource] = relationship(back_populates="permission")


class DatasetVersion(Base):
    __tablename__ = "dataset_versions"
    __table_args__ = (UniqueConstraint("data_source_id", "version"),)
    id: Mapped[int] = mapped_column(primary_key=True)
    data_source_id: Mapped[int] = mapped_column(ForeignKey("data_sources.id"))
    version: Mapped[str] = mapped_column(String(60))
    released_at: Mapped[datetime | None]
    imported_at: Mapped[datetime] = mapped_column(default=utcnow)
    citation: Mapped[str | None] = mapped_column(Text)
    source_url: Mapped[str | None] = mapped_column(String(500))
    checksum: Mapped[str | None] = mapped_column(String(128))
    record_count: Mapped[int | None] = mapped_column(Integer)
    meta: Mapped[dict] = mapped_column("metadata", JSONB, default=dict)


class IngestionRun(Base):
    __tablename__ = "ingestion_runs"
    __table_args__ = (Index("ix_ingestion_runs_source_started", "data_source_id", "started_at"),)
    id: Mapped[int] = mapped_column(primary_key=True)
    data_source_id: Mapped[int] = mapped_column(ForeignKey("data_sources.id"))
    task: Mapped[str] = mapped_column(String(80))
    status: Mapped[str] = mapped_column(String(20), default="running")  # running | success | partial | failed
    started_at: Mapped[datetime] = mapped_column(default=utcnow)
    finished_at: Mapped[datetime | None]
    window_start: Mapped[datetime | None]
    window_end: Mapped[datetime | None]
    records_fetched: Mapped[int] = mapped_column(Integer, default=0)
    records_inserted: Mapped[int] = mapped_column(Integer, default=0)
    records_skipped: Mapped[int] = mapped_column(Integer, default=0)
    error: Mapped[str | None] = mapped_column(Text)
    meta: Mapped[dict] = mapped_column("metadata", JSONB, default=dict)


class IngestionBatch(Base):
    """One expected unit of upstream data (e.g. a GDELT 15-minute file).

    Expected batches are enumerated from the checkpoint so that missed
    intervals are visible (``pending``/``failed``) and recovered later.
    """

    __tablename__ = "ingestion_batches"
    __table_args__ = (
        UniqueConstraint("data_source_id", "batch_key"),
        Index("ix_ingestion_batches_status", "data_source_id", "status"),
    )
    id: Mapped[int] = mapped_column(primary_key=True)
    data_source_id: Mapped[int] = mapped_column(ForeignKey("data_sources.id"))
    batch_key: Mapped[str] = mapped_column(String(80))
    batch_time: Mapped[datetime | None]
    status: Mapped[str] = mapped_column(String(20), default="pending")  # pending | done | failed | missing_upstream
    attempts: Mapped[int] = mapped_column(Integer, default=0)
    records: Mapped[int] = mapped_column(Integer, default=0)
    inserted: Mapped[int] = mapped_column(Integer, default=0)
    checksum: Mapped[str | None] = mapped_column(String(128))
    source_url: Mapped[str | None] = mapped_column(String(500))
    last_error: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(default=utcnow)
    processed_at: Mapped[datetime | None]
    run_id: Mapped[int | None] = mapped_column(ForeignKey("ingestion_runs.id"))


class IngestionCheckpoint(Base):
    __tablename__ = "ingestion_checkpoints"
    __table_args__ = (UniqueConstraint("data_source_id", "stream"),)
    id: Mapped[int] = mapped_column(primary_key=True)
    data_source_id: Mapped[int] = mapped_column(ForeignKey("data_sources.id"))
    stream: Mapped[str] = mapped_column(String(120), default="default")
    marker: Mapped[str | None] = mapped_column(String(200))
    marker_time: Mapped[datetime | None]
    updated_at: Mapped[datetime] = mapped_column(default=utcnow, onupdate=utcnow)


class SourceDocument(Base):
    """A referenced source document (e.g. the article URL behind a GDELT event).

    Only metadata and the link are stored — never full text — to respect
    publisher copyright.
    """

    __tablename__ = "source_documents"
    id: Mapped[int] = mapped_column(primary_key=True)
    url: Mapped[str] = mapped_column(Text)
    url_hash: Mapped[str] = mapped_column(String(64), unique=True)
    domain: Mapped[str | None] = mapped_column(String(255), index=True)
    title: Mapped[str | None] = mapped_column(Text)
    language: Mapped[str | None] = mapped_column(String(40))
    source_country_id: Mapped[int | None] = mapped_column(ForeignKey("countries.id"))
    published_at: Mapped[datetime | None]
    first_seen_at: Mapped[datetime] = mapped_column(default=utcnow)
    data_source_id: Mapped[int | None] = mapped_column(ForeignKey("data_sources.id"))
    meta: Mapped[dict] = mapped_column("metadata", JSONB, default=dict)


class AppSetting(Base):
    """Runtime-editable settings (watchlist, thresholds, feature flags)."""

    __tablename__ = "app_settings"
    key: Mapped[str] = mapped_column(String(100), primary_key=True)
    value: Mapped[dict | list | str | int | float | bool | None] = mapped_column(JSONB)
    updated_at: Mapped[datetime] = mapped_column(default=utcnow, onupdate=utcnow)
    updated_by: Mapped[str | None] = mapped_column(String(100))


class AuditLog(Base):
    __tablename__ = "audit_log"
    id: Mapped[int] = mapped_column(primary_key=True)
    actor: Mapped[str] = mapped_column(String(100))
    action: Mapped[str] = mapped_column(String(100))
    target: Mapped[str | None] = mapped_column(String(200))
    details: Mapped[dict] = mapped_column(JSONB, default=dict)
    created_at: Mapped[datetime] = mapped_column(default=utcnow)
