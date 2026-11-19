"""GeoRisk model service — serves versioned ML model packages over HTTP.

Completely independent of the GeoRisk application: it only reads model packages from ``MODEL_DIR``::

    <MODEL_DIR>/<model_name>/<version>/model.txt        LightGBM booster
                                      /calibrator.json  {"x": [...], "y": [...]} isotonic mapping
                                      /metadata.json    {"features": [...], "label_definition": ..., ...}

The newest version (lexicographic) of each model is served. Dropping a new version folder in and calling
``POST /reload`` (or restarting) switches models without code changes.

Callers send factor values; the service returns calibrated probabilities (plus, optionally, per-factor
contributions explaining each prediction).
"""

from __future__ import annotations

import json
import os
import threading
from dataclasses import dataclass
from pathlib import Path

import lightgbm as lgb
import numpy as np
from fastapi import FastAPI, HTTPException
from pydantic import BaseModel, Field

MODEL_DIR = Path(os.environ.get("MODEL_DIR", "/models"))
# Packages shipped with the service (used when MODEL_DIR has none or older versions of a model).
BUNDLED_DIR = Path(os.environ.get("BUNDLED_MODEL_DIR", str(Path(__file__).resolve().parents[1] / "models")))


@dataclass
class LoadedModel:
    name: str
    version: str
    booster: lgb.Booster
    features: list[str]
    calibrator: dict
    metadata: dict

    def predict(self, rows: list[dict], explain: bool = False, top_k: int = 5) -> list[dict]:
        x = np.array([[_num(r.get(f)) for f in self.features] for r in rows], dtype=float)
        raw = self.booster.predict(x)
        prob = np.interp(raw, self.calibrator["x"], self.calibrator["y"]) if self.calibrator.get("x") else raw
        out = []
        contrib = self.booster.predict(x, pred_contrib=True) if explain else None
        for i in range(len(rows)):
            item = {"probability": float(prob[i]), "raw_score": float(raw[i])}
            if explain:
                c = contrib[i][:-1]  # last column = bias
                order = np.argsort(-np.abs(c))[:top_k]
                item["contributions"] = [
                    {"factor": self.features[j], "value": None if np.isnan(x[i, j]) else float(x[i, j]),
                     "effect": float(c[j]), "direction": "raises" if c[j] > 0 else "lowers"} for j in order]
            out.append(item)
        return out


def _num(v) -> float:
    try:
        return float(v) if v is not None else np.nan
    except (TypeError, ValueError):
        return np.nan


class Registry:
    """Serves the newest version of each model found under any of ``roots`` (e.g. a shared volume written by
    retraining, plus the packages bundled with the service)."""

    def __init__(self, *roots: Path):
        self.roots = [r for r in roots if r]
        self.models: dict[str, LoadedModel] = {}
        self.lock = threading.Lock()

    def _candidates(self) -> dict[str, list[Path]]:
        out: dict[str, list[Path]] = {}
        for root in self.roots:
            if not root.exists():
                continue
            for model_dir in (p for p in root.iterdir() if p.is_dir()):
                for v in model_dir.iterdir():
                    if (v / "model.txt").exists() and (v / "metadata.json").exists():
                        out.setdefault(model_dir.name, []).append(v)
        return out

    def load(self) -> dict[str, str]:
        found: dict[str, LoadedModel] = {}
        for name, versions in sorted(self._candidates().items()):
            v = sorted(versions, key=lambda p: p.name)[-1]  # newest version across all roots
            meta = json.loads((v / "metadata.json").read_text())
            cal = json.loads((v / "calibrator.json").read_text()) if (v / "calibrator.json").exists() else {}
            found[name] = LoadedModel(name, v.name, lgb.Booster(model_file=str(v / "model.txt")),
                                      list(meta["features"]), cal, meta)
        with self.lock:
            self.models = found
        return {k: m.version for k, m in found.items()}

    def get(self, name: str) -> LoadedModel:
        m = self.models.get(name)
        if m is None:
            raise HTTPException(404, f"model '{name}' not found; available: {sorted(self.models)}")
        return m


registry = Registry(MODEL_DIR, BUNDLED_DIR)
app = FastAPI(title="GeoRisk Model Service", version="0.1.0",
              description="Serves versioned GeoRisk ML models. Predictions are experimental research estimates.")


@app.on_event("startup")
def _startup() -> None:
    registry.load()


class PredictRequest(BaseModel):
    model: str = "conflict_onset"
    rows: list[dict] = Field(..., min_length=1, max_length=5000, description="Factor values keyed by feature name; "
                             "an optional 'id' key is echoed back")
    explain: bool = False
    top_k: int = Field(5, ge=1, le=20)


@app.get("/health")
def health():
    return {"status": "ok", "models_loaded": {k: m.version for k, m in registry.models.items()}}


@app.get("/models")
def models():
    return {"items": [{"name": m.name, "version": m.version, "features": m.features,
                       **{k: m.metadata.get(k) for k in ("label_definition", "applies_to", "prediction_window",
                                                         "trained_at", "test", "provenance", "disclaimer", "algorithm")}}
                      for m in registry.models.values()]}


@app.post("/reload")
def reload():
    return {"loaded": registry.load()}


@app.post("/predict")
def predict(req: PredictRequest):
    m = registry.get(req.model)
    unknown = sorted({k for r in req.rows for k in r} - set(m.features) - {"id"})
    preds = m.predict(req.rows, explain=req.explain, top_k=req.top_k)
    for row, p in zip(req.rows, preds, strict=True):
        if "id" in row:
            p["id"] = row["id"]
        p["missing_factors"] = sum(1 for f in m.features if _num(row.get(f)) != _num(row.get(f)))
    return {"model": m.name, "model_version": m.version, "predictions": preds, "ignored_fields": unknown,
            "disclaimer": m.metadata.get("disclaimer")}
