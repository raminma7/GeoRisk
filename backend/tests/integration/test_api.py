"""REST API contract tests (FastAPI TestClient against the test database)."""

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import update

from app.core.config import get_settings
from app.ingestion.models import DataSource, SourcePermission
from app.main import app
from app.workers.pipeline import run_monitoring_pipeline
from tests.conftest import FIXTURE_NOW
from tests.integration.test_monitoring import scenario


@pytest.fixture
def client(ingested):
    scenario(ingested)
    run_monitoring_pipeline(now=FIXTURE_NOW)
    return TestClient(app)


def test_health(client):
    assert client.get("/health").json() == {"status": "ok"}
    assert client.get("/ready").json()["database"] is True


def test_overview_populates_without_selection(client):
    r = client.get("/api/v1/overview").json()
    assert r["counts"]["active_crises"] >= 1
    card = r["priority_situations"][0]
    for k in ("title", "countries", "region", "priority", "indicator_change", "evidence_confidence", "last_updated_at"):
        assert k in card
    assert "not a probability" in card["priority"]["note"]
    assert "not independently verified" in r["disclaimer"]
    assert r["data_freshness"]["gdelt_events"]["last_success_at"]


def test_country_search_and_detail(client):
    items = client.get("/api/v1/countries", params={"q": "ukr"}).json()["items"]
    assert items[0]["iso3"] == "UKR"
    d = client.get("/api/v1/countries/UA").json()
    assert d["country"]["iso3"] == "UKR" and d["modules"]["economy"]["status"] == "planned"
    assert client.get("/api/v1/countries/ZZZ").status_code == 404
    ev = client.get("/api/v1/countries/UKR/events", params={"role": "occurred_in", "start": "2026-11-18"}).json()
    assert ev["total"] >= 1
    assert all(e["location"]["country"] == "UKR" for e in ev["items"])


def test_compare_many_countries(client):
    r = client.post("/api/v1/countries/compare",
                    json={"countries": ["UKR", "RUS", "BLR", "USA", "CHN"], "start": "2026-11-18"}).json()
    assert len(r["countries"]) == 5
    assert {"nodes", "edges"} <= set(r["interaction_graph"])
    assert any(set(c["selected_countries_involved"]) >= {"UKR", "RUS"} for c in r["shared_crises"])
    assert set(r["timelines"]) == {"UKR", "RUS", "BLR", "USA", "CHN"}


def test_crisis_detail_timeline_and_sources(client):
    crises = client.get("/api/v1/crises").json()["items"]
    cid = next(c["id"] for c in crises if "UKR" in [x["iso3"] for x in c["countries"]])
    d = client.get(f"/api/v1/crises/{cid}").json()
    assert d["participants"] and d["priority_detail"]["components"] and d["evidence_log"]
    t = client.get(f"/api/v1/crises/{cid}/timeline").json()
    assert t["events"]["total"] >= 12 and "multi_source_reported" in t["verification_legend"]
    by_uid = client.get(f"/api/v1/crises/{d['uid']}").json()
    assert by_uid["id"] == cid


def test_alerts_include_evidence(client):
    alerts = client.get("/api/v1/monitoring/alerts").json()["items"]
    assert alerts
    assert all(a["evidence"]["supporting_event_clusters"] for a in alerts if a["crisis_id"])


def test_events_map_and_news(client):
    fc = client.get("/api/v1/events", params={"view": "map", "start": "2026-11-18"}).json()
    assert fc["type"] == "FeatureCollection" and fc["features"]
    assert client.get("/api/v1/events", params={"quad_class": 4, "start": "2026-11-18"}).status_code == 200
    assert client.get("/api/v1/news", params={"clustered": True}).status_code == 200


def test_ai_unavailable_without_key(client, monkeypatch):
    crisis = client.get("/api/v1/crises").json()["items"][0]
    r = client.get(f"/api/v1/crises/{crisis['id']}/analysis").json()
    assert r["status"] == "unavailable" and r["analysis"] is None
    assert client.post("/api/v1/analysis/multi-country", json={"countries": ["UKR", "RUS"]}).status_code == 401
    monkeypatch.setenv("API_KEYS", "analyst:ana-secret")
    get_settings.cache_clear()
    try:
        r = client.post("/api/v1/analysis/multi-country", json={"countries": ["UKR", "RUS"]},
                        headers={"X-API-Key": "ana-secret"}).json()
        assert r["status"] == "unavailable"
        r = client.post("/api/v1/analysis/multi-country", json={"countries": ["UKR"], "mode": "combined"},
                        headers={"X-API-Key": "ana-secret"})
        assert r.status_code == 501
    finally:
        get_settings.cache_clear()


def test_economy_is_planned_and_has_no_values(client):
    r = client.get("/api/v1/economy/global")
    assert r.status_code == 501 and r.json()["status"] == "planned"
    ind = client.get("/api/v1/economy/indicators").json()
    assert ind["values_available"] is False and all("value" not in i for i in ind["items"])


def test_role_based_access(client, monkeypatch):
    monkeypatch.setenv("API_KEYS", "admin:adm-secret,analyst:ana-secret")
    get_settings.cache_clear()
    try:
        assert client.put("/api/v1/settings/watchlist", json={"value": ["UKR"]}).status_code == 401
        assert client.put("/api/v1/settings/watchlist", json={"value": ["UKR"]},
                          headers={"X-API-Key": "ana-secret"}).status_code == 403
        assert client.put("/api/v1/settings/watchlist", json={"value": ["ukr", "RUS"]},
                          headers={"X-API-Key": "adm-secret"}).json()["value"] == ["RUS", "UKR"]
        assert client.get("/api/v1/settings", headers={"X-API-Key": "bad"}).status_code == 401
        s = client.get("/api/v1/settings").json()
        assert "adm-secret" not in str(s) and s["credentials"]["api_keys"] == "configured"
    finally:
        get_settings.cache_clear()


def test_export_respects_licensing(client, ingested, analyst_key):
    assert client.post("/api/v1/datasets/export", json={"kind": "events"}).status_code == 401
    r = client.post("/api/v1/datasets/export", json={"kind": "events", "format": "csv", "start": "2026-11-18"},
                    headers=analyst_key)
    assert r.status_code == 200 and r.json()["rows"] == 550
    assert "gdelt_events" in r.json()["licensing"]["sources"]
    assert client.get(r.json()["download"], headers=analyst_key).status_code == 200
    src = ingested.query(DataSource).filter_by(key="gdelt_events").one()
    ingested.execute(update(SourcePermission).where(SourcePermission.data_source_id == src.id).values(allow_export=False))
    ingested.commit()
    r = client.post("/api/v1/datasets/export", json={"kind": "events", "format": "parquet"}, headers=analyst_key)
    assert r.status_code == 403


def test_monitoring_status(client):
    st = client.get("/api/v1/monitoring/status").json()
    gd = next(s for s in st["sources"] if s["key"] == "gdelt_events")
    assert gd["batches"]["done"] == 2 and gd["last_success_at"]
    acled = next(s for s in st["sources"] if s["key"] == "acled")
    assert acled["enabled"] is False and acled["permissions"]["allow_ml_training"] is False
    assert st["coverage"]["events_24h"] >= 0


def test_openapi(client):
    paths = client.get("/openapi.json").json()["paths"]
    for p in ("/api/v1/overview", "/api/v1/monitoring/priorities", "/api/v1/monitoring/alerts", "/api/v1/monitoring/status",
              "/api/v1/countries", "/api/v1/countries/{code}", "/api/v1/countries/{code}/events",
              "/api/v1/countries/{code}/analysis", "/api/v1/countries/compare", "/api/v1/analysis/multi-country",
              "/api/v1/crises", "/api/v1/crises/{crisis_id}", "/api/v1/crises/{crisis_id}/timeline",
              "/api/v1/crises/{crisis_id}/analysis", "/api/v1/news", "/api/v1/events", "/api/v1/economy/global",
              "/api/v1/economy/countries/{code}", "/api/v1/economy/indicators", "/api/v1/economy/compare",
              "/api/v1/datasets", "/api/v1/datasets/export"):
        assert p in paths, p
