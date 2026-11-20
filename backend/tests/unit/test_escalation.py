"""Escalation labels ('will the crisis get worse?'), site-aligned features, levels and plain explanations."""

import numpy as np
import pandas as pd

from app.ml.escalation import index_labels, site_features, tier_of
from app.ml.explain import format_value, reason
from app.ml.predictions import level_for


def cm_for(deaths: list[float]) -> pd.DataFrame:
    d = np.array(deaths, dtype=float)
    return pd.DataFrame({"iso3": "AAA", "month": pd.date_range("2000-01-01", periods=len(d), freq="MS"),
                         "all_deaths": d, "ns_events": 0.0, "os_events": 0.0})


def test_tiers_match_the_site():
    assert list(tier_of([0, 99, 100, 999, 1_000, 9_999, 10_000])) == [0, 0, 1, 1, 2, 2, 3]


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


def test_outlook_rule():
    from types import SimpleNamespace as N

    from app.ml.predictions import outlook

    assert outlook(N(probability=0.20), N(probability=0.10), 0.15, 0.27)["direction"] == "worse"
    assert outlook(N(probability=0.05), N(probability=0.40), 0.15, 0.27)["direction"] == "better"
    same = outlook(N(probability=0.05), N(probability=0.10), 0.15, 0.27)
    assert same["direction"] == "same" and same["label"] == "Likely to stay the same"
    # both pass: the stronger signal relative to its threshold wins (0.30/0.15 = 2.0 vs 0.40/0.27 = 1.5)
    assert outlook(N(probability=0.30), N(probability=0.40), 0.15, 0.27)["direction"] == "worse"
    assert outlook(None, N(probability=0.90), 0.15, 0.27)["direction"] == "better"  # Critical: only 'better' applies


def test_index_labels_worse_better_and_limits():
    cuts = {"critical": 90.0, "high": 80.0, "elevated": 70.0}
    months = pd.date_range("2000-01-01", periods=4, freq="MS")
    hist = pd.DataFrame({"iso3": ["AAA"] * 4 + ["BBB"] * 4 + ["CCC"] * 4, "month": list(months) * 3,
                         "idx_score": [50, 50, 50, 50, 85, 85, 85, 85, 95, 95, 95, 95],
                         "idx_score_next_3m": [75, 60, None, None, 72, 86, 95, 95, 85, 96, 96, 96],
                         "idx_score_next_6m": [None] * 12})
    lab = index_labels(hist, cuts)
    a, b, c = lab.iloc[0:4], lab.iloc[4:8], lab.iloc[8:12]
    assert list(a["worse_3m"].iloc[:2]) == [1.0, 0.0] and a["worse_3m"].iloc[2:].isna().all()  # Low -> Moderate
    assert a["better_3m"].isna().all()                                                     # Low cannot get better
    assert b["better_3m"].iloc[0] == 1.0 and b["worse_3m"].iloc[2] == 1.0                    # High -> Moderate / Critical
    assert c["worse_3m"].isna().all() and c["better_3m"].iloc[0] == 1.0                      # Critical cannot get worse
    assert list(lab["idx_level"].iloc[[0, 4, 8]]) == [0, 2, 3]


def test_guard_never_contradicts_this_weeks_news():
    from app.ml.predictions import guard

    better = {"direction": "better", "label": "Could get better", "chance": 0.87}
    worse = {"direction": "worse", "label": "Could get worse", "chance": 0.3}
    # Iran case: the model expects improvement from lagged deaths, but fighting picked up this week
    assert guard(better, "up", None)["direction"] == "uncertain"
    assert guard(better, None, 1.8)["direction"] == "uncertain"
    assert "picked up" in guard(better, "up", 1.0)["note"]
    assert guard(better, "down", 0.9)["direction"] == "better"          # consistent with the news
    assert guard(worse, "down", 0.5)["direction"] == "uncertain"        # clearly calmer now
    assert guard(worse, "down", 1.2)["direction"] == "worse"            # calmer day, but fighting news still up
    assert guard({"direction": "same", "label": "Likely to stay the same", "chance": 0.9}, "up", 2.0)["direction"] == "same"
