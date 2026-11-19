"""Economic Intelligence schema — FUTURE MODULE (Phase 5+).

Foundational tables only, so the shared schema is stable. No economic
ingestion runs in the MVP and no economic values are displayed. Design rules
encoded here:

* every observation keeps unit, currency, seasonal adjustment, frequency,
  period, publication date, availability date and vintage;
* revisions are new rows (``economic_data_revisions``), never overwrites;
* missing values are ``NULL`` with a ``quality_status`` — never zero.
"""

from __future__ import annotations

from datetime import date, datetime

from sqlalchemy import BigInteger, Date, Float, ForeignKey, Index, String, Text, UniqueConstraint
from sqlalchemy.dialects.postgresql import ARRAY, JSONB
from sqlalchemy.orm import Mapped, mapped_column

from app.core.db import Base, utcnow


class EconomicIndicatorDefinition(Base):
    __tablename__ = "economic_indicator_definitions"
    __table_args__ = (UniqueConstraint("data_source_id", "source_indicator_code"),)
    id: Mapped[int] = mapped_column(primary_key=True)
    indicator_key: Mapped[str] = mapped_column(String(100), index=True)  # canonical: gdp_current_usd, cpi_yoy ...
    name: Mapped[str] = mapped_column(String(300))
    description: Mapped[str | None] = mapped_column(Text)
    data_source_id: Mapped[int] = mapped_column(ForeignKey("data_sources.id"))
    source_indicator_code: Mapped[str] = mapped_column(String(120))  # e.g. NY.GDP.MKTP.CD
    unit: Mapped[str] = mapped_column(String(80))
    currency: Mapped[str | None] = mapped_column(String(10))
    price_basis: Mapped[str | None] = mapped_column(String(40))  # current | constant_2015 | ppp
    seasonal_adjustment: Mapped[str | None] = mapped_column(String(10))  # SA | NSA | SAAR
    frequency: Mapped[str] = mapped_column(String(10))  # A | Q | M | W | D
    aggregation_method: Mapped[str | None] = mapped_column(String(40))  # sum | gdp_weighted_mean | not_aggregatable
    release_schedule: Mapped[str | None] = mapped_column(String(120))
    meta: Mapped[dict] = mapped_column("metadata", JSONB, default=dict)


class EconomicDataRelease(Base):
    __tablename__ = "economic_data_releases"
    id: Mapped[int] = mapped_column(primary_key=True)
    data_source_id: Mapped[int] = mapped_column(ForeignKey("data_sources.id"))
    release_key: Mapped[str] = mapped_column(String(160), unique=True)
    released_at: Mapped[datetime]
    dataset_version_id: Mapped[int | None] = mapped_column(ForeignKey("dataset_versions.id"))
    meta: Mapped[dict] = mapped_column("metadata", JSONB, default=dict)


class EconomicObservation(Base):
    __tablename__ = "economic_observations"
    __table_args__ = (
        UniqueConstraint("indicator_id", "scope_type", "scope_key", "period_start", "vintage_at"),
        Index("ix_econ_obs_scope", "scope_type", "scope_key", "indicator_id", "period_start"),
    )
    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    indicator_id: Mapped[int] = mapped_column(ForeignKey("economic_indicator_definitions.id"))
    scope_type: Mapped[str] = mapped_column(String(20))  # country | region | country_group | global
    scope_key: Mapped[str] = mapped_column(String(60))  # ISO3, region code, ...
    country_id: Mapped[int | None] = mapped_column(ForeignKey("countries.id"))
    period_start: Mapped[date] = mapped_column(Date)
    period_end: Mapped[date] = mapped_column(Date)
    value: Mapped[float | None] = mapped_column(Float)  # NULL = missing, never 0
    quality_status: Mapped[str] = mapped_column(String(30), default="official")  # official | provisional | estimate | missing | forecast
    is_forecast: Mapped[bool] = mapped_column(default=False)
    published_at: Mapped[datetime | None]
    available_at: Mapped[datetime]
    vintage_at: Mapped[datetime]
    release_id: Mapped[int | None] = mapped_column(ForeignKey("economic_data_releases.id"))
    dataset_version_id: Mapped[int | None] = mapped_column(ForeignKey("dataset_versions.id"))
    ingested_at: Mapped[datetime] = mapped_column(default=utcnow)
    meta: Mapped[dict] = mapped_column("metadata", JSONB, default=dict)


class EconomicDataRevision(Base):
    __tablename__ = "economic_data_revisions"
    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    observation_id: Mapped[int] = mapped_column(ForeignKey("economic_observations.id"))
    previous_observation_id: Mapped[int | None] = mapped_column(ForeignKey("economic_observations.id"))
    previous_value: Mapped[float | None] = mapped_column(Float)
    new_value: Mapped[float | None] = mapped_column(Float)
    revised_at: Mapped[datetime]
    reason: Mapped[str | None] = mapped_column(Text)


class TradeFlow(Base):
    __tablename__ = "trade_flows"
    __table_args__ = (Index("ix_trade_flows_pair", "reporter_country_id", "partner_country_id", "period_start"),)
    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    data_source_id: Mapped[int] = mapped_column(ForeignKey("data_sources.id"))
    reporter_country_id: Mapped[int] = mapped_column(ForeignKey("countries.id"))
    partner_country_id: Mapped[int | None] = mapped_column(ForeignKey("countries.id"))
    flow: Mapped[str] = mapped_column(String(10))  # import | export | re_export
    commodity_code: Mapped[str | None] = mapped_column(String(20))
    classification: Mapped[str | None] = mapped_column(String(20))  # HS | SITC
    period_start: Mapped[date] = mapped_column(Date)
    period_end: Mapped[date] = mapped_column(Date)
    value: Mapped[float | None] = mapped_column(Float)
    unit: Mapped[str] = mapped_column(String(40))
    currency: Mapped[str | None] = mapped_column(String(10))
    available_at: Mapped[datetime]
    vintage_at: Mapped[datetime]


class CommodityObservation(Base):
    __tablename__ = "commodity_observations"
    __table_args__ = (UniqueConstraint("commodity_key", "data_source_id", "observed_on", "vintage_at"),)
    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    commodity_key: Mapped[str] = mapped_column(String(60))
    data_source_id: Mapped[int] = mapped_column(ForeignKey("data_sources.id"))
    observed_on: Mapped[date] = mapped_column(Date)
    value: Mapped[float | None] = mapped_column(Float)
    unit: Mapped[str] = mapped_column(String(40))
    currency: Mapped[str | None] = mapped_column(String(10))
    available_at: Mapped[datetime]
    vintage_at: Mapped[datetime]


class EconomicEvent(Base):
    """Economic events (sanctions, central-bank decisions, data releases)."""

    __tablename__ = "economic_events"
    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    event_type: Mapped[str] = mapped_column(String(60))  # sanction | rate_decision | trade_restriction | data_release
    title: Mapped[str] = mapped_column(String(300))
    country_iso3s: Mapped[list[str]] = mapped_column(ARRAY(String(3)), default=list)
    occurred_on: Mapped[date] = mapped_column(Date)
    available_at: Mapped[datetime]
    data_source_id: Mapped[int | None] = mapped_column(ForeignKey("data_sources.id"))
    source_url: Mapped[str | None] = mapped_column(Text)
    meta: Mapped[dict] = mapped_column("metadata", JSONB, default=dict)


class EconomicAnalysis(Base):
    __tablename__ = "economic_analyses"
    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    scope_type: Mapped[str] = mapped_column(String(30))
    scope_key: Mapped[str] = mapped_column(String(300))
    provider: Mapped[str] = mapped_column(String(40))
    model: Mapped[str] = mapped_column(String(100))
    prompt_version: Mapped[str] = mapped_column(String(20))
    output: Mapped[dict | None] = mapped_column(JSONB)
    evidence_ids: Mapped[dict] = mapped_column(JSONB, default=dict)
    created_at: Mapped[datetime] = mapped_column(default=utcnow)


class CrossDomainLink(Base):
    """Explicit, time-windowed link between geopolitical and economic entities
    (Phase 7). A link records *association in time*, never causation."""

    __tablename__ = "cross_domain_links"
    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    left_type: Mapped[str] = mapped_column(String(40))  # crisis | geopolitical_event | economic_event | indicator
    left_id: Mapped[str] = mapped_column(String(80))
    right_type: Mapped[str] = mapped_column(String(40))
    right_id: Mapped[str] = mapped_column(String(80))
    window_start: Mapped[date] = mapped_column(Date)
    window_end: Mapped[date] = mapped_column(Date)
    relation: Mapped[str] = mapped_column(String(40), default="temporal_association")
    method: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(default=utcnow)
