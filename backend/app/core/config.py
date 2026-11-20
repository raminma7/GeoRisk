"""Environment-based configuration.

All schedules, thresholds, feature flags and credentials are configured here.
Runtime-editable overrides (watchlist, thresholds, feature flags) are stored in
the ``app_settings`` table and merged by :mod:`app.core.runtime_settings`.
"""

from __future__ import annotations

from functools import lru_cache
from typing import Annotated, Literal

from pydantic import Field, SecretStr, field_validator
from pydantic_settings import BaseSettings, NoDecode, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", env_prefix="", extra="ignore")

    # --- core -------------------------------------------------------------
    app_name: str = "GeoRisk"
    environment: Literal["development", "test", "production"] = "development"
    log_level: str = "INFO"
    log_json: bool = True
    database_url: str = "postgresql+psycopg://georisk:georisk@localhost:5432/georisk"
    redis_url: str = "redis://localhost:6379/0"
    # NoDecode: accept plain comma-separated env values (e.g. CORS_ORIGINS=http://a,http://b); JSON lists also work
    cors_origins: Annotated[list[str], NoDecode] = ["http://localhost:3000"]
    # Also allow the dashboard from any local port (e.g. a frontend moved to localhost:3008). Set to "" to disable.
    cors_origin_regex: str = r"https?://(localhost|127\.0\.0\.1)(:\d+)?"

    # --- security ---------------------------------------------------------
    # Comma separated "role:key" pairs, e.g. "admin:abc123,analyst:def456".
    api_keys: SecretStr = SecretStr("")
    # Admin panel sign-in. Change the password in the panel or here (ADMIN_PASSWORD).
    admin_email: str = "mehrialinia_ramin@yahoo.com"
    admin_password: SecretStr = SecretStr("georisk-admin")
    public_read: bool = True  # read-only endpoints available without a key
    rate_limit_default: str = "240/minute"
    rate_limit_expensive: str = "20/minute"

    # --- feature flags ----------------------------------------------------
    feature_economy: bool = False
    feature_combined_analysis: bool = False  # stays off until economy is implemented
    feature_acled: bool = False

    # --- monitoring watchlist / coverage ----------------------------------
    # ISO3 codes. Ingestion is global; the watchlist only drives DOC news queries
    # and extra AI attention.
    watchlist: Annotated[list[str], NoDecode] = Field(
        default_factory=lambda: ["UKR", "RUS", "ISR", "PSE", "IRN", "SDN", "CHN", "TWN", "IND", "PAK", "COD", "VEN"]
    )

    # --- GDELT ------------------------------------------------------------
    gdelt_base_url: str = "https://data.gdeltproject.org/gdeltv2"
    gdelt_doc_url: str = "https://api.gdeltproject.org/api/v2/doc/doc"
    gdelt_events_interval_minutes: int = 15
    gdelt_doc_interval_minutes: int = 15
    gdelt_max_recovery_batches: int = 96  # 24h of 15-min slots per run
    gdelt_initial_lookback_batches: int = 4  # on first run, how many recent slots to import
    gdelt_doc_min_interval_seconds: float = 5.5  # GDELT asks for <= 1 request / 5s
    gdelt_doc_max_records: int = 250
    # topic key -> GDELT DOC query. Country queries are generated from the watchlist.
    gdelt_doc_topic_queries: dict[str, str] = Field(
        default_factory=lambda: {
            "armed_conflict": "(airstrike OR missile OR shelling OR troops OR \"armed clashes\")",
            "diplomacy": "(sanctions OR ambassador OR embassy OR \"foreign minister\")",
            "de_escalation": "(ceasefire OR \"peace talks\" OR truce OR negotiations)",
            "instability": "(coup OR insurgents OR militants OR \"state of emergency\")",
        }
    )
    gdelt_doc_country_terms: str = "(military OR conflict OR sanctions OR diplomatic OR ceasefire OR attack)"
    gdelt_doc_lookback_hours: int = 36  # max window per query (DOC index lags real time)
    http_timeout_seconds: float = 60.0
    http_max_retries: int = 4

    # --- UCDP -------------------------------------------------------------
    ucdp_downloads_url: str = "https://ucdp.uu.se/downloads"
    ucdp_ged_version: str = "26.1"
    ucdp_min_year: int = 1989

    # --- ACLED (optional, disabled by default) ----------------------------
    acled_email: str | None = None
    acled_api_key: SecretStr | None = None

    # --- AI ---------------------------------------------------------------
    ai_provider: Literal["none", "anthropic", "openai"] = "none"
    anthropic_api_key: SecretStr | None = None
    anthropic_model: str = "claude-opus-5-5"
    anthropic_effort: str = "medium"
    anthropic_fallbacks: bool = True  # server-side refusal fallbacks (beta)
    openai_api_key: SecretStr | None = None
    openai_model: str = "gpt-4.1-mini"
    ai_max_evidence_items: int = 40
    ai_max_output_tokens: int = 3000
    ai_crisis_interval_minutes: int = 60
    ai_max_crises_per_run: int = 8
    ai_daily_report_hour_utc: int = 6

    # --- crisis engine ----------------------------------------------------
    crisis_lookback_hours: int = 72
    crisis_attach_threshold: float = 0.55
    crisis_review_threshold: float = 0.40
    crisis_min_event_clusters: int = 6
    crisis_min_sources: int = 4
    crisis_dormant_after_days: int = 10
    crisis_participant_min_events: int = 3

    # --- priority engine --------------------------------------------------
    priority_interval_minutes: int = 15
    priority_weights: dict[str, float] = Field(
        default_factory=lambda: {
            "severity": 0.30,
            "change_vs_baseline": 0.20,
            "corroboration": 0.15,
            "diplomatic_signals": 0.10,
            "evidence_completeness": 0.05,
            "freshness": 0.10,
            "new_development": 0.10,
        }
    )
    # Crisis index (country severity): weight of each dimension; see app/ml/crisis_index.py
    crisis_index_weights: dict[str, float] = Field(
        default_factory=lambda: {"violence": 0.4, "military": 0.2, "tension": 0.2, "unrest": 0.1, "momentum": 0.1}
    )
    priority_levels: dict[str, float] = Field(
        default_factory=lambda: {"critical": 75.0, "high": 55.0, "elevated": 35.0}
    )
    alert_threshold_level: str = "high"
    baseline_days: int = 30

    # --- ML / datasets ----------------------------------------------------
    feature_snapshot_hour_utc: int = 2
    export_dir: str = "/data/exports"
    model_dir: str = "/data/models"
    factor_dir: str = "/data/factors"  # external factor files (python -m app.cli download-factors)

    # --- separate model service (experimental early-warning model) --------
    model_service_url: str = "http://localhost:8100"
    predictions_day_of_month: int = 22  # after UCDP Candidate monthly releases (~20th)

    # --- notifications ----------------------------------------------------
    alert_webhook_url: str | None = None

    @field_validator("watchlist", mode="before")
    @classmethod
    def _split_csv(cls, v):  # allow WATCHLIST=UKR,RUS in env (or a JSON list)
        if isinstance(v, str):
            v = v.strip()
            if v.startswith("["):
                import json

                return [str(x).upper() for x in json.loads(v)]
            return [x.strip().upper() for x in v.split(",") if x.strip()]
        return v

    @field_validator("cors_origins", mode="before")
    @classmethod
    def _split_origins(cls, v):
        if isinstance(v, str):
            v = v.strip()
            if v.startswith("["):
                import json

                return json.loads(v)
            return [x.strip() for x in v.split(",") if x.strip()]
        return v

    def parsed_api_keys(self) -> dict[str, str]:
        """Return mapping key -> role."""
        out: dict[str, str] = {}
        raw = self.api_keys.get_secret_value()
        for pair in raw.split(","):
            pair = pair.strip()
            if not pair or ":" not in pair:
                continue
            role, key = pair.split(":", 1)
            out[key.strip()] = role.strip()
        return out


@lru_cache
def get_settings() -> Settings:
    return Settings()
