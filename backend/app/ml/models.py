"""Historical ML foundation: point-in-time features, independent labels,
versioned datasets/models/evaluations."""

from __future__ import annotations

from datetime import date, datetime

from sqlalchemy import BigInteger, Date, Float, ForeignKey, Index, Integer, String, Text, UniqueConstraint
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from app.core.db import Base, utcnow


class FeatureSnapshot(Base):
    __tablename__ = "feature_snapshots"
    __table_args__ = (
        UniqueConstraint("entity_type", "entity_key", "as_of", "feature_set_version"),
        Index("ix_feature_snapshots_asof", "feature_set_version", "as_of"),
    )
    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    entity_type: Mapped[str] = mapped_column(String(20))  # country | country_pair | crisis | region | global
    entity_key: Mapped[str] = mapped_column(String(200))
    as_of: Mapped[datetime]  # features use only data with available_at <= as_of
    feature_set_version: Mapped[str] = mapped_column(String(40))
    features: Mapped[dict] = mapped_column(JSONB)
    source_vintages: Mapped[dict] = mapped_column(JSONB, default=dict)  # dataset versions / release vintages used
    created_at: Mapped[datetime] = mapped_column(default=utcnow)


class OutcomeLabel(Base):
    """Independently defined outcome labels (from UCDP, never from LLM output)."""

    __tablename__ = "outcome_labels"
    __table_args__ = (
        UniqueConstraint("entity_type", "entity_key", "label_name", "label_version", "window_start"),
    )
    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    entity_type: Mapped[str] = mapped_column(String(20))
    entity_key: Mapped[str] = mapped_column(String(200))
    label_name: Mapped[str] = mapped_column(String(80))
    label_version: Mapped[str] = mapped_column(String(20))
    window_start: Mapped[date] = mapped_column(Date)
    window_end: Mapped[date] = mapped_column(Date)
    value: Mapped[float] = mapped_column(Float)
    source_dataset_version_id: Mapped[int | None] = mapped_column(ForeignKey("dataset_versions.id"))
    created_at: Mapped[datetime] = mapped_column(default=utcnow)


class TrainingDataset(Base):
    __tablename__ = "training_datasets"
    __table_args__ = (UniqueConstraint("name", "version"),)
    id: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str] = mapped_column(String(120))
    version: Mapped[str] = mapped_column(String(40))
    definition: Mapped[dict] = mapped_column(JSONB)  # feature set, label, entity, period, filters
    row_count: Mapped[int] = mapped_column(Integer, default=0)
    file_path: Mapped[str | None] = mapped_column(Text)
    file_format: Mapped[str | None] = mapped_column(String(10))
    checksum: Mapped[str | None] = mapped_column(String(64))
    licensing: Mapped[dict] = mapped_column(JSONB, default=dict)
    created_at: Mapped[datetime] = mapped_column(default=utcnow)


class ModelVersion(Base):
    __tablename__ = "model_versions"
    __table_args__ = (UniqueConstraint("name", "version"),)
    id: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str] = mapped_column(String(120))
    version: Mapped[str] = mapped_column(String(40))
    algorithm: Mapped[str] = mapped_column(String(60))
    params: Mapped[dict] = mapped_column(JSONB, default=dict)
    training_dataset_id: Mapped[int | None] = mapped_column(ForeignKey("training_datasets.id"))
    artifact_path: Mapped[str | None] = mapped_column(Text)
    random_seed: Mapped[int | None] = mapped_column(Integer)
    status: Mapped[str] = mapped_column(String(20), default="experimental")  # experimental — never "validated" automatically
    notes: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(default=utcnow)


class ModelEvaluation(Base):
    __tablename__ = "model_evaluations"
    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    model_version_id: Mapped[int] = mapped_column(ForeignKey("model_versions.id", ondelete="CASCADE"), index=True)
    split_kind: Mapped[str] = mapped_column(String(30))  # walk_forward | out_of_time | holdout
    fold: Mapped[int] = mapped_column(Integer, default=0)
    train_start: Mapped[date | None] = mapped_column(Date)
    train_end: Mapped[date | None] = mapped_column(Date)
    test_start: Mapped[date | None] = mapped_column(Date)
    test_end: Mapped[date | None] = mapped_column(Date)
    metrics: Mapped[dict] = mapped_column(JSONB)
    baseline_metrics: Mapped[dict] = mapped_column(JSONB, default=dict)
    notes: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(default=utcnow)


class MlPrediction(Base):
    """Stored output of a model-service batch run (read by the app; the service itself is never on the page path)."""

    __tablename__ = "ml_predictions"
    __table_args__ = (
        UniqueConstraint("model_name", "model_version", "iso3", "window_start"),
        Index("ix_ml_predictions_window", "model_name", "window_start"),
    )
    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    model_name: Mapped[str] = mapped_column(String(80))
    model_version: Mapped[str] = mapped_column(String(80))
    iso3: Mapped[str] = mapped_column(String(3), index=True)
    window_start: Mapped[date] = mapped_column(Date)
    window_end: Mapped[date] = mapped_column(Date)
    data_through: Mapped[date] = mapped_column(Date)
    status: Mapped[str] = mapped_column(String(30))  # predicted | ongoing_conflict
    probability: Mapped[float | None] = mapped_column(Float)
    raw_score: Mapped[float | None] = mapped_column(Float)
    rank: Mapped[int | None] = mapped_column(Integer)
    out_of: Mapped[int | None] = mapped_column(Integer)
    times_average: Mapped[float | None] = mapped_column(Float)
    level: Mapped[str | None] = mapped_column(String(20))  # very_high | high | elevated | low
    reasons: Mapped[list] = mapped_column(JSONB, default=list)
    created_at: Mapped[datetime] = mapped_column(default=utcnow)
