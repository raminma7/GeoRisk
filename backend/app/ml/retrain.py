"""Monthly retraining of the 4 outlook models, with a quality gate.

Each month (after the new UCDP data is in) the models are retrained on the newest data. A new model replaces the
served one only if it is at least as good on the same recent out-of-time years (walk-forward, last 5 test years):

* same recipe (features + settings) as the served model, more data → the refreshed model is used (a sanity floor on
  ROC-AUC protects against broken input data);
* a different recipe (e.g. the site's own daily snapshots once a year of them exists, ``site_*`` features) → used
  only if ROC-AUC does not drop and the Brier score does not rise.

Nothing is deleted: old packages stay in MODEL_DIR (the service loads the newest), so going back is a folder delete.
Every decision is kept in ``app_settings.ml_retraining`` (shown on the admin page).
"""

from __future__ import annotations

import pandas as pd
from sqlalchemy.orm import Session

from app.core.db import utcnow
from app.core.logging import get_logger
from app.ml import escalation as E

log = get_logger(__name__)

SETTING_KEY = "ml_retraining"
ROC_FLOOR = 0.70            # a refreshed model below this on recent years means something is wrong with the data
ROC_TOLERANCE = 0.0         # a new recipe must not lose ROC-AUC …
BRIER_TOLERANCE = 0.0005    # … nor get a worse Brier score (beyond rounding noise)
SITE_MIN_DAYS = 365         # site snapshots needed before they are tried as features
SITE_FEATURES = ["site_score_mean", "site_score_max", "site_up_days_share", "site_down_days_share",
                 "site_situations_max", "site_surge_max"]


# ------------------------------------------------------------------ site snapshots → monthly features
def site_snapshot_features(session: Session) -> pd.DataFrame | None:
    """Monthly summaries of the daily country snapshots, for month t from the days of month t-1 (data before t).
    None while fewer than SITE_MIN_DAYS days are stored."""
    from app.ml.crisis_index import snapshot_history

    snap = snapshot_history(session)
    if snap.empty or snap["date"].nunique() < SITE_MIN_DAYS:
        return None
    return monthly_site_features(snap)


def monthly_site_features(snap: pd.DataFrame) -> pd.DataFrame:
    snap = snap.assign(month=snap["date"].dt.to_period("M").dt.to_timestamp(),
                       up=(snap["trend"] == "up").astype(float), down=(snap["trend"] == "down").astype(float))
    g = snap.groupby(["iso3", "month"])
    m = pd.DataFrame({"site_score_mean": g["score"].mean(), "site_score_max": g["score"].max(),
                      "site_up_days_share": g["up"].mean(), "site_down_days_share": g["down"].mean(),
                      "site_situations_max": g["situations"].max(), "site_surge_max": g["recent_surge"].max()})
    m = m.reset_index()
    m["month"] = m["month"] + pd.DateOffset(months=1)   # row t sees the days of month t-1
    return m


# ------------------------------------------------------------------ gate
def served_models() -> dict[str, dict]:
    from app.ml.predictions import _service

    return {m["name"]: m for m in _service("/models")["items"]}


def recent_score(df: pd.DataFrame, feats: list[str], params: dict, kind: str, h: int) -> dict:
    years = sorted(df.loc[df[f"{kind}_{h}m"].notna(), "month"].dt.year.unique())[-E.RECENT_YEARS:]
    pred = E.walk_forward(df, feats, h, [int(y) for y in years], kind, params)
    return E.recent_metrics(pred, f"{kind}_{h}m")


def decide(champion: dict | None, challenger: dict, same_recipe: bool, more_data: bool) -> tuple[bool, str]:
    """champion / challenger: recent metrics (roc_auc, brier)."""
    roc, brier = challenger.get("roc_auc"), challenger.get("brier")
    if roc is None:
        return False, "not enough recent outcomes to test"
    if champion is None:
        return roc >= ROC_FLOOR, "no served model" if roc >= ROC_FLOOR else f"test ROC-AUC {roc} below {ROC_FLOOR}"
    if same_recipe:
        if not more_data:
            return False, "no new data since the served model"
        if roc < ROC_FLOOR:
            return False, f"refreshed model's ROC-AUC {roc} below {ROC_FLOOR}: check the input data"
        return True, "same recipe, more data"
    c_roc, c_brier = champion.get("roc_auc"), champion.get("brier")
    if roc + ROC_TOLERANCE < (c_roc or 0):
        return False, f"ROC-AUC {roc} < served {c_roc}"
    if c_brier is not None and brier is not None and brier > c_brier + BRIER_TOLERANCE:
        return False, f"Brier {brier} > served {c_brier}"
    return True, f"better on recent years (ROC-AUC {c_roc} → {roc}, Brier {c_brier} → {brier})"


def retrain_if_better(session: Session, df: pd.DataFrame | None = None, served: dict | None = None,
                      reload: bool = True) -> dict:
    from app.ingestion.models import AppSetting
    from app.ml.predictions import _service

    df = df if df is not None else E.build_frame(session)
    site = site_snapshot_features(session)
    recipes = {"standard": E.features()}
    if site is not None:
        df = df.merge(site, on=["iso3", "month"], how="left")
        recipes["with_site_snapshots"] = E.features() + SITE_FEATURES
    served = served if served is not None else served_models()
    params = dict(E.PARAMS)
    results: dict = {"run_at": utcnow().isoformat(), "models": {}}
    exported = 0
    for kind, h in [(k, h) for k in E.KINDS for h in E.HORIZONS]:
        name = f"{E.KINDS[kind][1]}_{h}m"
        cur = served.get(name)
        champ_feats = [f for f in (cur or {}).get("features", []) if f in df.columns]
        champ_params = {**E.PARAMS, **((cur or {}).get("params") or {})}
        champ = recent_score(df, champ_feats, champ_params, kind, h) if champ_feats else None
        # best challenger recipe on the recent years
        best = None
        for rname, feats in recipes.items():
            same = cur is not None and feats == cur.get("features") and params == champ_params
            score = champ if same and champ else recent_score(df, feats, params, kind, h)
            if best is None or (score.get("roc_auc") or 0) > (best[2].get("roc_auc") or 0):
                best = (rname, feats, score, same)
        rname, feats, score, same = best
        rows_now = int(df[(df[f"{E.KINDS[kind][0]}_{h}m"] == 1) & df[f"{kind}_{h}m"].notna()].shape[0])
        more = cur is None or rows_now > int((cur.get("training_rows") or 0))
        ok, why = decide(champ, score, same, more)
        entry = {"served_version": (cur or {}).get("version"), "recipe": rname, "served_recent": champ,
                 "new_recent": score, "replaced": ok, "reason": why}
        if ok:
            pkg = E.train(df, feats, params)[name]
            E.export(pkg)
            entry["new_version"] = pkg["meta"]["version"]
            exported += 1
        results["models"][name] = entry
        log.info("retrain_decision", model=name, replaced=ok, reason=why)
    if exported and reload:
        try:
            results["reloaded"] = _service("/reload", {})["loaded"]
        except Exception as exc:  # the next service restart loads it anyway
            results["reload_error"] = str(exc)
    row = session.get(AppSetting, SETTING_KEY)
    if row is None:
        row = AppSetting(key=SETTING_KEY)
        session.add(row)
    history = (row.value or {}).get("history", []) if isinstance(row.value, dict) else []
    row.value = {"last": results, "history": ([*history, {"run_at": results["run_at"], **{
        n: {"replaced": e["replaced"], "reason": e["reason"]} for n, e in results["models"].items()}}])[-24:]}
    row.updated_at = utcnow()
    row.updated_by = "retrain"
    session.flush()
    return results


def stored(session: Session) -> dict:
    from app.ingestion.models import AppSetting

    row = session.get(AppSetting, SETTING_KEY)
    return row.value if row and isinstance(row.value, dict) else {}

