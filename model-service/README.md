# GeoRisk model service

A small, stand-alone HTTP service that serves versioned GeoRisk ML models. It has **no dependency on the
GeoRisk application code**: it only reads model packages from `MODEL_DIR`.

```
<MODEL_DIR>/crisis_escalation_3m/<version>/model.txt        LightGBM booster
                                    /calibrator.json  probability calibration
                                    /metadata.json    ordered features, label definition, test metrics
```

The newest version of each model is served. To deploy a new model, add a new version folder and call
`POST /reload` (or restart). The GeoRisk backend creates packages with `python -m app.cli train-escalation-models` (crisis_escalation_3m and crisis_escalation_6m: "will this country's crisis get worse in the next 3 / 6 months?").

## Endpoints
- `GET /health`: status and loaded versions
- `GET /models`: features, label definition, test quality, provenance
- `POST /predict`: `{"model": "crisis_escalation_3m", "rows": [{"id": "ERI", "<feature>": value, ...}], "explain": true}`
  returns a calibrated `probability`, the `raw_score`, and (with `explain`) the top factor contributions per row.
- `POST /reload`: rescan `MODEL_DIR`

## Run locally

```bash
pip install -e ".[dev]"
```

```bash
MODEL_DIR=/path/to/models uvicorn model_service.main:app --port 8100
```

```bash
pytest
```

Predictions are experimental research estimates, not probabilities of war.
