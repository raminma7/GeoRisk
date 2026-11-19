"""The simple 'Today' overview: plain headlines, reasons, main vs other signals."""

from fastapi.testclient import TestClient

from app.crises.models import Crisis
from app.main import app
from app.monitoring import plain
from tests.conftest import FIXTURE_NOW


def add(db, uid, core, loc, level, mat, src, conf="medium"):
    db.add(Crisis(uid=uid, title="raw title", status="active", core_country_iso3s=core, location_country_iso3s=loc,
                  first_observed_at=FIXTURE_NOW, last_event_at=FIXTURE_NOW, algorithm_version="t", priority_level=level,
                  current_priority={"high": 60, "elevated": 40}.get(level, 10),
                  current_indicators={"material_conflict_24h": mat, "diplomatic_signals_24h": 4,
                                      "independent_sources_24h": src, "evidence_confidence_label": conf}, meta={}))


def test_today_groups_and_explains(db, monkeypatch):
    monkeypatch.setattr(plain, "conflict_deaths_12m", lambda s: {"UKR": 30_000})
    add(db, "CR-1", ["RUS", "UKR"], ["UKR"], "elevated", 12, 80)
    add(db, "CR-2", ["CHN", "USA"], ["CHN"], "high", 5, 60)
    add(db, "CR-3", ["GBR"], ["GBR"], "high", 30, 90)          # domestic -> other signals
    add(db, "CR-4", ["IRN", "KWT"], ["KWT"], "elevated", 2, 3, conf="low")  # weak -> other signals
    db.commit()
    r = TestClient(app).get("/api/v1/today").json()
    heads = [x["headline"] for x in r["situations"]]
    assert heads == ["Armed conflict in Ukraine, involving Russia", "Tensions between China and United States"]
    top = r["situations"][0]
    assert top["severity"] == {"level": "critical", "label": "Critical"}  # 30k verified deaths outrank news intensity
    assert top["reasons"][0].startswith("About 30,000 people killed")
    assert "12 reports of attacks" in top["reasons"][1]
    assert len(top["activity"]) == 7
    assert {x["headline"] for x in r["other_signals"]} == {"Unrest and violence reports in United Kingdom",
                                                          "Tensions between Iran and Kuwait"}
    assert r["summary"]["situations"] == 2 and r["summary"]["serious"] == 2
    assert r["what_changed"]["note"]  # monitoring just started: explained instead of an empty box
    crisis_id = r["situations"][0]["id"]
    d = TestClient(app).get(f"/api/v1/crises/{crisis_id}").json()
    assert d["story"]["headline"] == heads[0] and d["recent_incidents"] == []
