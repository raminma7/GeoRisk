"""Early-warning API ('could get worse in the next 3 / 6 months') reads stored predictions only (works with the model
service offline)."""

from datetime import date

from fastapi.testclient import TestClient

from app.crises.models import Crisis
from app.main import app
from app.ml import predictions as P
from app.ml.models import MlPrediction
from tests.conftest import FIXTURE_NOW


def store(db, iso3, rank, prob, level, status="predicted", tier="low", model=P.MODEL, end=date(2026, 12, 1)):
    db.add(MlPrediction(model_name=model, model_version="vTEST", iso3=iso3, window_start=date(2026, 10, 1),
                        window_end=end, data_through=date(2026, 9, 1), status=status, current_tier=tier,
                        probability=prob, raw_score=prob, rank=rank, out_of=3, times_average=prob / 0.01 if prob else None,
                        level=level, reasons=[{"factor": "nb_countries_in_conflict_l12", "label": "Neighbours at war",
                                               "value": "2", "direction": "raises", "weight": 0.3}]))


def test_watch_list_country_and_spillover(db):
    store(db, "POL", 1, 0.05, "high")
    store(db, "BLR", 2, 0.02, "elevated", tier="elevated")
    store(db, "MDA", 3, 0.001, "low")
    store(db, "UKR", None, None, None, status="top_tier", tier="critical")
    store(db, "POL", 1, 0.08, "high", model=P.MODELS[6], end=date(2027, 3, 1))
    db.add(Crisis(uid="CR-T-1", title="test", status="active", core_country_iso3s=["UKR"], location_country_iso3s=["UKR"],
                  first_observed_at=FIXTURE_NOW, last_event_at=FIXTURE_NOW, algorithm_version="t", current_indicators={},
                  meta={}))
    db.commit()
    c = TestClient(app)
    wl = c.get("/api/v1/predictions").json()
    assert [i["iso3"] for i in wl["items"]] == ["POL", "BLR", "MDA"]
    assert wl["already_critical"] == [{"iso3": "UKR", "name": "Ukraine"}]
    assert wl["meta"]["window"] == {"start": "2026-10-01", "end": "2026-12-01"}
    assert wl["meta"]["window_6m"] == {"start": "2026-10-01", "end": "2027-03-01"}
    assert wl["items"][0]["six_months"]["level"] == "high" and wl["items"][1]["six_months"] is None
    assert "Research estimate" in wl["meta"]["explainer"]["caveat"]
    assert [i["iso3"] for i in c.get("/api/v1/predictions", params={"group": "crisis"}).json()["items"]] == ["BLR"]
    assert [i["iso3"] for i in c.get("/api/v1/predictions", params={"group": "calm"}).json()["items"]] == ["POL", "MDA"]
    assert c.get("/api/v1/predictions", params={"level": "high"}).json()["items"][0]["iso3"] == "POL"
    one = c.get("/api/v1/predictions/countries/PL").json()
    assert one["rank"] == 1 and one["reasons"][0]["label"] == "Neighbours at war" and one["history"]
    assert one["current_tier"] == "low" and one["six_months"]["times_average"] == 8.0
    detail = c.get("/api/v1/countries/POL").json()
    assert detail["ml_risk"]["level"] == "high"
    crisis_id = db.query(Crisis).one().id
    sp = c.get(f"/api/v1/crises/{crisis_id}").json()["spillover_risk"]
    assert sp[0]["iso3"] == "POL" and "UKR" in sp[0]["borders"]  # neighbours of Ukraine, highest risk first
    assert {x["iso3"] for x in sp} >= {"POL", "BLR", "MDA"}
    assert c.get("/api/v1/overview").json()["early_warning"]["items"][0]["iso3"] == "POL"


def test_no_predictions_yet(db):
    r = TestClient(app).get("/api/v1/predictions").json()
    assert r["items"] == [] and r["meta"] is None and "setup_steps" in r


def test_setup_status_shown_while_bootstrapping(db):
    from app.ml import predictions as P

    assert P.needs_bootstrap(db) is True
    P._set_status(state="running", step="news", step_label=P.STEP_LABELS["news"], progress={"done": 50, "total": 700})
    db.expire_all()
    assert P.needs_bootstrap(db) is False  # a fresh running setup is not started twice
    r = TestClient(app).get("/api/v1/predictions").json()
    assert r["meta"] is None and r["setup"]["step"] == "news" and r["setup"]["progress"]["total"] == 700
    assert [s["id"] for s in r["setup_steps"]] == P.STEPS
