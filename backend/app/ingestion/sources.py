"""Catalog of data sources and their licensing permissions.

Permissions are enforced by AI processing (``allow_ai_processing``), ML
dataset construction (``allow_ml_training``) and exports (``allow_export``).
"""

from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.ingestion.models import DataSource, SourcePermission

GDELT_CITATION = (
    "The GDELT Project (https://www.gdeltproject.org/). Leetaru, K. and Schrodt, P. (2013). GDELT: Global Data "
    "on Events, Language, and Tone, 1979-2012. ISA Annual Convention."
)
UCDP_CITATION = (
    "Davies, S., Pettersson, T., Sollenberg, M., & Öberg, M. (2025). Organized violence 1989–2024, and the "
    "challenges of identifying civilian victims. Journal of Peace Research 62(4). Sundberg, R. and Melander, E. "
    "(2013) Introducing the UCDP Georeferenced Event Dataset. Journal of Peace Research 50(4)."
)

SOURCES: list[dict] = [
    {
        "key": "gdelt_events", "name": "GDELT Events 2.0", "module": "geopolitics", "kind": "event_stream",
        "homepage_url": "https://www.gdeltproject.org/", "expected_interval_minutes": 15,
        "license": "GDELT data is free and open for unlimited use with citation.",
        "citation": GDELT_CITATION, "enabled": True,
        "perm": dict(allow_ai_processing=True, allow_ml_training=True, allow_export=True, allow_redistribution=True),
    },
    {
        "key": "gdelt_doc", "name": "GDELT DOC 2.0 API", "module": "geopolitics", "kind": "news_search",
        "homepage_url": "https://blog.gdeltproject.org/gdelt-doc-2-0-api-debuts/", "expected_interval_minutes": 15,
        "license": "Article metadata and links only. Article content remains copyright of each publisher; "
                   "full text is never stored or redistributed.",
        "citation": GDELT_CITATION, "enabled": True,
        "perm": dict(allow_ai_processing=True, allow_ml_training=True, allow_export=True,
                     allow_redistribution=False, store_full_text=False,
                     notes="Exports include headline/URL metadata only."),
    },
    {
        "key": "gdelt_v1_history", "name": "GDELT 1.0 Events archive (historical news aggregates)", "module": "geopolitics",
        "kind": "dataset", "homepage_url": "https://data.gdeltproject.org/events/index.html", "expected_interval_minutes": None,
        "license": "GDELT data is free and open for unlimited use with citation.", "citation": GDELT_CITATION, "enabled": True,
        "perm": dict(allow_ai_processing=True, allow_ml_training=True, allow_export=True, allow_redistribution=True),
    },
    {
        "key": "ucdp_ged", "name": "UCDP Georeferenced Event Dataset", "module": "geopolitics", "kind": "dataset",
        "homepage_url": "https://ucdp.uu.se/", "expected_interval_minutes": None,
        "license": "UCDP data is free to use with citation (CC BY 4.0).",
        "citation": UCDP_CITATION, "enabled": True,
        "perm": dict(allow_ai_processing=True, allow_ml_training=True, allow_export=True, allow_redistribution=True),
    },
    {
        "key": "acled", "name": "ACLED (optional)", "module": "geopolitics", "kind": "dataset",
        "homepage_url": "https://acleddata.com/", "expected_interval_minutes": None,
        "license": "Restricted. ACLED terms limit redistribution and use for AI/ML training, embeddings and "
                   "derived datasets. Disabled unless credentials and appropriate permissions are configured.",
        "citation": "Armed Conflict Location & Event Data (ACLED), https://acleddata.com", "enabled": False,
        "perm": dict(allow_ai_processing=False, allow_ml_training=False, allow_export=False,
                     allow_redistribution=False, notes="Excluded from ML datasets, embeddings and exports."),
    },
    # --- Economic Intelligence (future; disabled) --------------------------
    {"key": "world_bank", "name": "World Bank Indicators API", "module": "economy", "kind": "indicator_api",
     "homepage_url": "https://datahelpdesk.worldbank.org/knowledgebase/articles/889392", "license": "CC BY 4.0",
     "enabled": False, "perm": dict(allow_redistribution=True)},
    {"key": "imf", "name": "IMF Data APIs", "module": "economy", "kind": "indicator_api",
     "homepage_url": "https://data.imf.org/en/Resource-Pages/IMF-API", "license": "IMF terms of use",
     "enabled": False, "perm": dict(allow_redistribution=False)},
    {"key": "oecd", "name": "OECD Data API", "module": "economy", "kind": "indicator_api",
     "homepage_url": "https://www.oecd.org/en/data/insights/data-explainers/2024/09/api.html",
     "license": "OECD terms and conditions", "enabled": False, "perm": dict(allow_redistribution=False)},
    {"key": "fred", "name": "FRED / ALFRED", "module": "economy", "kind": "indicator_api",
     "homepage_url": "https://fred.stlouisfed.org/docs/api/fred/", "license": "Series-specific; API key required",
     "enabled": False, "perm": dict(allow_redistribution=False)},
    {"key": "un_comtrade", "name": "UN Comtrade", "module": "economy", "kind": "indicator_api",
     "homepage_url": "https://comtradeplus.un.org/en/api-docs", "license": "UN Comtrade terms; API key required",
     "enabled": False, "perm": dict(allow_redistribution=False)},
]


def seed_sources(session: Session) -> None:
    for spec in SOURCES:
        spec = dict(spec)
        perm = spec.pop("perm", {})
        ds = session.scalar(select(DataSource).where(DataSource.key == spec["key"]))
        if not ds:
            ds = DataSource(config={}, **spec)
            session.add(ds)
            session.flush()
            session.add(SourcePermission(data_source_id=ds.id, **perm))
    session.flush()


def get_source(session: Session, key: str) -> DataSource:
    ds = session.scalar(select(DataSource).where(DataSource.key == key))
    if ds is None:
        seed_sources(session)
        ds = session.scalar(select(DataSource).where(DataSource.key == key))
    assert ds is not None
    return ds
