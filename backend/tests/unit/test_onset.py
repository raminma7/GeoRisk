"""Clean onset labels, risk levels and plain-language explanations."""

import numpy as np
import pandas as pd

from app.ml.enrichment import onset_labels
from app.ml.explain import format_value, reason
from app.ml.predictions import level_for


def cm_for(deaths: list[float]) -> pd.DataFrame:
    n = len(deaths)
    d = np.array(deaths, dtype=float)
    return pd.DataFrame({"iso3": "AAA", "month": pd.date_range("2000-01-01", periods=n, freq="MS"),
                         "sb_deaths": d, "sb_events": (d > 0).astype(float)})


def test_onset_requires_24_quiet_months_and_25_deaths():
    deaths = [0] * 30 + [10, 20, 0] + [0] * 5
    lab = onset_labels(cm_for(deaths)).set_index("month")
    t = pd.Timestamp("2002-07-01")  # month index 30 (first deaths)
    assert lab.loc[t, "onset_risk_major"] == 1
    assert lab.loc[t, "onset_major"] == 1.0  # 10 + 20 + 0 = 30 deaths in t..t+2
    assert lab.loc[t - pd.DateOffset(months=1), "onset_major"] == 1.0  # window t-1..t+1 holds all 30 deaths
    assert lab.loc[t - pd.DateOffset(months=2), "onset_major"] == 0.0  # window t-2..t holds only 10 deaths
    assert lab.loc[t + pd.DateOffset(months=1), "onset_risk_major"] == 0  # deaths in previous 24 months


def test_small_incident_is_not_a_new_war():
    deaths = [0] * 30 + [3, 0, 0] + [0] * 5
    lab = onset_labels(cm_for(deaths)).set_index("month")
    t = pd.Timestamp("2002-07-01")
    assert lab.loc[t, "onset_major"] == 0.0
    assert lab.loc[t, "minor_incident"] == 1.0


def test_labels_unknown_when_window_not_observed_and_no_history():
    lab = onset_labels(cm_for([0] * 30)).set_index("month")
    assert np.isnan(lab.iloc[-1]["onset_major"])  # t+1, t+2 not observed yet
    assert lab.iloc[10]["onset_risk_major"] == 0  # fewer than 24 months of history


def test_levels_and_reasons():
    assert level_for(12) == "very_high" and level_for(5) == "high" and level_for(2) == "elevated" and level_for(1) == "low"
    r = reason("nb_countries_in_conflict_l12", 3.0, 0.4)
    assert r == {"factor": "nb_countries_in_conflict_l12", "label": "Neighbours at war", "value": "3",
                 "direction": "raises", "weight": 0.4}
    assert format_value("wdi_log_gdp_per_capita", float(np.log(1500))) == "$1,500"
    assert format_value("months_since_sb", 240.0).startswith("never")
    assert format_value("news_violent_share", 0.097) == "9.7%"
    assert format_value("wdi_inflation_pct", None) == "no data"
