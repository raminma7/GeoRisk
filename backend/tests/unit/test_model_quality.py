"""Track record scoring, the retraining gate and the monthly summaries of the site's daily snapshots."""

import pandas as pd

from app.ml.retrain import ROC_FLOOR, decide, monthly_site_features
from app.ml.track_record import score


def test_track_record_counts_hits_against_the_level_when_predicted():
    # now = level when predicted; actual = level over the window
    preds = pd.DataFrame({"probability": [0.9, 0.8, 0.1, 0.05], "now": [1, 1, 2, 0], "actual": [2, 1, 3, 0]})
    s = score(preds, threshold=0.5, kind="worse")
    assert s["checked"] == 4 and s["happened"] == 2          # rows 1 and 3 rose
    assert s["said"] == 2 and s["said_and_happened"] == 1 and s["hit_rate"] == 0.5
    assert s["caught"] == 0.5 and s["roc_auc"] == 0.75
    b = score(preds.assign(actual=[0, 1, 2, 0]), threshold=0.5, kind="better")
    assert b["happened"] == 1 and b["said_and_happened"] == 1


def test_gate_keeps_the_served_model_unless_the_new_one_is_as_good():
    champ = {"roc_auc": 0.81, "brier": 0.080}
    assert decide(champ, {"roc_auc": 0.80, "brier": 0.079}, same_recipe=False, more_data=True)[0] is False
    assert decide(champ, {"roc_auc": 0.82, "brier": 0.085}, same_recipe=False, more_data=True)[0] is False
    assert decide(champ, {"roc_auc": 0.82, "brier": 0.079}, same_recipe=False, more_data=True)[0] is True
    # same recipe: refresh on new data, unless nothing new or the result is broken
    assert decide(champ, champ, same_recipe=True, more_data=True)[0] is True
    assert decide(champ, champ, same_recipe=True, more_data=False)[0] is False
    assert decide(champ, {"roc_auc": ROC_FLOOR - 0.01, "brier": 0.1}, same_recipe=True, more_data=True)[0] is False
    assert decide(None, {"roc_auc": 0.8, "brier": 0.1}, same_recipe=False, more_data=True)[0] is True
    assert decide(champ, {"n": 10}, same_recipe=False, more_data=True)[0] is False


def test_site_snapshot_features_for_month_t_come_from_month_t_minus_1():
    days = pd.date_range("2027-01-01", "2027-02-28", freq="D")
    snap = pd.DataFrame({"iso3": "AAA", "date": days, "score": [80.0 if d.month == 1 else 50.0 for d in days],
                         "trend": ["up" if d.day <= 10 and d.month == 1 else "flat" for d in days],
                         "situations": 2, "recent_surge": 1.2})
    m = monthly_site_features(snap).set_index("month")
    feb = m.loc[pd.Timestamp("2027-02-01")]
    assert feb["site_score_mean"] == 80.0 and round(feb["site_up_days_share"], 3) == round(10 / 31, 3)
    assert m.loc[pd.Timestamp("2027-03-01"), "site_score_mean"] == 50.0
    assert pd.Timestamp("2027-01-01") not in m.index
