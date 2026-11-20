"""Escalation labels ('will the crisis get worse?'), site-aligned features, levels and plain explanations."""

import numpy as np
import pandas as pd

from app.ml.escalation import escalation_labels, site_features, tier_of
from app.ml.explain import format_value, reason
from app.ml.predictions import level_for


def cm_for(deaths: list[float]) -> pd.DataFrame:
    d = np.array(deaths, dtype=float)
    return pd.DataFrame({"iso3": "AAA", "month": pd.date_range("2000-01-01", periods=len(d), freq="MS"),
                         "all_deaths": d, "ns_events": 0.0, "os_events": 0.0})


def test_tiers_match_the_site():
    assert list(tier_of([0, 99, 100, 999, 1_000, 9_999, 10_000])) == [0, 0, 1, 1, 2, 2, 3]


def test_quiet_country_with_new_violence_gets_worse():
    lab = escalation_labels(cm_for([0] * 14 + [10, 10, 10] + [0] * 6), 3).set_index("month")
    t = pd.Timestamp("2001-03-01")  # month 14: 30 deaths in t..t+2 -> 120 a year -> Moderate (from Low)
    assert lab.loc[t, "worse_3m"] == 1.0
    assert lab.loc[t - pd.DateOffset(months=1), "worse_3m"] == 0.0  # window holds 20 deaths -> 80 a year


def test_steady_war_is_not_worse_and_critical_cannot_rise():
    steady = escalation_labels(cm_for([200] * 30), 3)  # 2,400 a year: High, staying High
    assert set(steady["worse_3m"].dropna()) == {0.0}
    crit = escalation_labels(cm_for([2_000] * 30), 6)  # 24,000 a year: Critical
    assert set(crit["can_rise_6m"].iloc[12:]) == {0} and crit["worse_6m"].isna().all()


def test_labels_unknown_until_the_window_is_observed():
    lab = escalation_labels(cm_for([0] * 20), 6)
    assert lab["worse_6m"].iloc[-5:].isna().all() and lab["worse_6m"].iloc[:12].isna().all()


def test_site_features_use_only_the_past():
    f = site_features(cm_for([0] * 12 + [300] * 3 + [0] * 3), None).set_index("month")
    t = pd.Timestamp("2001-04-01")  # month 15: the three months with 300 deaths are t-3..t-1
    assert f.loc[t, "all_deaths_l3"] == 900 and f.loc[t, "tier_now"] == 1  # 900 a year so far -> Moderate
    assert f.loc[t, "deaths_trend_3v9"] > 10
    assert f.loc[pd.Timestamp("2001-01-01"), "all_deaths_l12"] == 0  # month 12 sees nothing of month 12


def test_levels_and_reasons():
    assert level_for(12) == "very_high" and level_for(5) == "high" and level_for(2) == "elevated" and level_for(1) == "low"
    r = reason("nb_countries_in_conflict_l12", 3.0, 0.4)
    assert r == {"factor": "nb_countries_in_conflict_l12", "label": "Neighbours at war", "value": "3",
                 "direction": "raises", "weight": 0.4}
    assert format_value("wdi_log_gdp_per_capita", float(np.log(1500))) == "$1,500"
    assert format_value("tier_now", 2.0) == "High" and format_value("news_violent_vs_normal", 1.84) == "1.8×"
    assert format_value("wdi_inflation_pct", None) == "no data"
