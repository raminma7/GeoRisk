"""Early-warning API reads stored predictions only (works with the model service offline)."""

from datetime import date

from fastapi.testclient import TestClient

from app.crises.models import Crisis
from app.ml.models import MlPrediction
from app.main import app
from tests.conftest import FIXTURE_NOW


def store(db, iso3, rank, prob, level, status="predicted"):
    db.add(MlPrediction(model_name="conflict_onset", model_version="vTEST", iso3=iso3, window_start=date(2026, 10, 1),
                        window_end=date(2026, 12, 1), data_through=date(2026, 9, 1), status=status,
                        probability=prob, raw_score=prob, rank=rank, out_of=3, times_average=prob / 0.01 if prob else None,
                        level=level, reasons=[{"factor": "nb_countries_in_conflict_l12", "label": "Neighbours at war",
                                               "value": "2", "direction": "raises", "weight": 0.3}]))


def test_watch_list_country_and_spillover(db):
    store(db, "POL", 1, 0.05, "high")
    store(db, "BLR", 2, 0.02, "elevated")
    store(db, "MDA", 3, 0.001, "low")
    store(db, "UKR", None, None, None, status="ongoing_conflict")
    db.add(Crisis(uid="CR-T-1", title="test", status="active", core_country_iso3s=["UKR"], location_country_iso3s=["UKR"],
                  first_observed_at=FIXTURE_NOW, last_event_at=FIXTURE_NOW, algorithm_version="t", current_indicators={},
                  meta={}))
    db.commit()
    c = TestClient(app)
    wl = c.get("/api/v1/predictions").json()
    assert [i["iso3"] for i in wl["items"]] == ["POL", "BLR", "MDA"]
    assert wl["ongoing_conflict"] == [{"iso3": "UKR", "name": "Ukraine"}]
    assert wl["meta"]["window"] == {"start": "2026-10-01", "end": "2026-12-01"}
    assert "not a forecast of war" in wl["meta"]["explainer"]["caveat"]
    assert c.get("/api/v1/predictions", params={"level": "high"}).json()["items"][0]["iso3"] == "POL"
    one = c.get("/api/v1/predictions/countries/PL").json()
    assert one["rank"] == 1 and one["reasons"][0]["label"] == "Neighbours at war" and one["history"]
    detail = c.get("/api/v1/countries/POL").json()
    assert detail["ml_risk"]["level"] == "high"
    crisis_id = db.query(Crisis).one().id
    sp = c.get(f"/api/v1/crises/{crisis_id}").json()["spillover_risk"]
    assert sp[0]["iso3"] == "POL" and "UKR" in sp[0]["borders"]  # neighbours of Ukraine, highest risk first
    assert {x["iso3"] for x in sp} >= {"POL", "BLR", "MDA"}
    assert c.get("/api/v1/overview").json()["early_warning"]["items"][0]["iso3"] == "POL"


def test_no_predictions_yet(db):
    r = TestClient(app).get("/api/v1/predictions").json()
    assert r["items"] == [] and r["meta"] is None
