"""Model service contract tests with a tiny synthetic model package (test-only)."""

import json

import lightgbm as lgb
import numpy as np
import pytest
from fastapi.testclient import TestClient


@pytest.fixture
def client(tmp_path, monkeypatch):
    rng = np.random.default_rng(0)
    x = rng.normal(size=(400, 2))
    y = (x[:, 0] + 0.2 * rng.normal(size=400) > 1).astype(int)
    booster = lgb.train({"objective": "binary", "verbose": -1, "num_leaves": 4}, lgb.Dataset(x, y), num_boost_round=20)
    v = tmp_path / "conflict_onset" / "v20260101-test"
    v.mkdir(parents=True)
    booster.save_model(str(v / "model.txt"))
    (v / "calibrator.json").write_text(json.dumps({"x": [0.0, 1.0], "y": [0.0, 0.5]}))
    (v / "metadata.json").write_text(json.dumps({"features": ["a", "b"], "disclaimer": "test model"}))
    import model_service.main as svc

    monkeypatch.setattr(svc, "registry", svc.Registry(tmp_path))
    svc.registry.load()
    return TestClient(svc.app), booster


def test_health_and_models(client):
    c, _ = client
    assert c.get("/health").json()["models_loaded"] == {"conflict_onset": "v20260101-test"}
    assert c.get("/models").json()["items"][0]["features"] == ["a", "b"]


def test_predict_matches_booster_and_calibrates(client):
    c, booster = client
    r = c.post("/predict", json={"rows": [{"id": "X", "a": 2.0, "b": 0.0}, {"a": -2.0, "b": 0.0}]}).json()
    raw = booster.predict(np.array([[2.0, 0.0], [-2.0, 0.0]]))
    assert r["model_version"] == "v20260101-test"
    assert r["predictions"][0]["id"] == "X"
    assert np.allclose([p["raw_score"] for p in r["predictions"]], raw)
    assert np.allclose([p["probability"] for p in r["predictions"]], raw * 0.5)  # calibrator applied
    assert r["predictions"][0]["probability"] > r["predictions"][1]["probability"]


def test_explain_missing_and_unknown_fields(client):
    c, _ = client
    r = c.post("/predict", json={"rows": [{"a": 2.0, "zzz": 1}], "explain": True, "top_k": 2}).json()
    p = r["predictions"][0]
    assert p["missing_factors"] == 1 and r["ignored_fields"] == ["zzz"]
    assert {x["factor"] for x in p["contributions"]} <= {"a", "b"}
    assert c.post("/predict", json={"model": "nope", "rows": [{"a": 1}]}).status_code == 404


def test_bundled_fallback_and_newer_shared_version_wins(tmp_path):
    import model_service.main as svc

    def pkg(root, version):
        rng = np.random.default_rng(1)
        x = rng.normal(size=(200, 1))
        b = lgb.train({"objective": "binary", "verbose": -1}, lgb.Dataset(x, (x[:, 0] > 0).astype(int)), num_boost_round=5)
        d = root / "conflict_onset" / version
        d.mkdir(parents=True)
        b.save_model(str(d / "model.txt"))
        (d / "metadata.json").write_text(json.dumps({"features": ["a"]}))

    shared, bundled = tmp_path / "shared", tmp_path / "bundled"
    pkg(bundled, "v20260101-bundled")
    assert svc.Registry(shared, bundled).load() == {"conflict_onset": "v20260101-bundled"}  # empty shared volume
    pkg(shared, "v20270101-retrained")
    assert svc.Registry(shared, bundled).load() == {"conflict_onset": "v20270101-retrained"}


def test_real_bundled_package_loads():
    import model_service.main as svc

    loaded = svc.Registry(svc.BUNDLED_DIR).load()
    assert "conflict_onset" in loaded
