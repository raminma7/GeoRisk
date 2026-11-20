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


def test_outlook_for_every_country_with_filters(db):
    # 'could get worse' model (3 months): decision threshold 0.15 when no model info is stored
    store(db, "POL", 1, 0.20, "high")                                             # -> worse
    store(db, "BLR", 2, 0.02, "elevated", tier="elevated")
    store(db, "MDA", 3, 0.01, "low")                                              # -> same
    store(db, "UKR", None, None, None, status="top_tier", tier="critical")
    # 'could get better' model (3 months): threshold 0.27; Low countries cannot get better
    store(db, "BLR", 1, 0.40, "high", tier="elevated", model=P.CALM_MODELS[3])    # -> better
    store(db, "UKR", 2, 0.05, "low", tier="critical", model=P.CALM_MODELS[3])     # -> same (stays Critical)
    for iso in ("POL", "MDA"):
        store(db, iso, None, None, None, status="bottom_tier", model=P.CALM_MODELS[3])
    store(db, "POL", 1, 0.30, "high", model=P.MODELS[6], end=date(2027, 3, 1))
    db.add(Crisis(uid="CR-T-1", title="test", status="active", core_country_iso3s=["UKR"], location_country_iso3s=["UKR"],
                  first_observed_at=FIXTURE_NOW, last_event_at=FIXTURE_NOW, algorithm_version="t", current_indicators={},
                  meta={}))
    db.commit()
    c = TestClient(app)
    wl = c.get("/api/v1/predictions").json()
    got = {i["iso3"]: (i["current_tier"], i["direction"]) for i in wl["items"]}
    assert got == {"POL": ("low", "worse"), "BLR": ("elevated", "better"), "MDA": ("low", "same"),
                   "UKR": ("critical", "same")}
    assert [i["iso3"] for i in wl["items"]][:2] == ["POL", "BLR"]  # worse first, then better, then same
    assert wl["counts"] == {"worse": 1, "better": 1, "same": 2}
    assert wl["meta"]["window"] == {"start": "2026-10-01", "end": "2026-12-01"}
    assert wl["items"][0]["six_months"]["direction"] == "worse" and wl["items"][0]["chance"] == 0.2
    assert wl["items"][0]["reasons"][0]["label"] == "Neighbours at war"
    assert [i["iso3"] for i in c.get("/api/v1/predictions", params={"direction": "better"}).json()["items"]] == ["BLR"]
    assert [i["iso3"] for i in c.get("/api/v1/predictions", params={"direction": "same"}).json()["items"]] == ["UKR", "MDA"]
    one = c.get("/api/v1/predictions/countries/PL").json()
    assert one["outlook"]["direction"] == "worse" and one["outlook_6m"]["direction"] == "worse"
    assert c.get("/api/v1/countries/BLR").json()["ml_risk"]["outlook"]["label"] == "Could get better"
    crisis_id = db.query(Crisis).one().id
    sp = c.get(f"/api/v1/crises/{crisis_id}").json()["spillover_risk"]
    assert sp[0]["iso3"] == "POL" and "UKR" in sp[0]["borders"]  # neighbours of Ukraine, most at risk first


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
