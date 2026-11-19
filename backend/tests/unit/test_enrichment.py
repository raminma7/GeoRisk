"""Enrichment: news aggregation, no-future-data lag rules, dynamics."""

from datetime import date

import numpy as np
import pandas as pd

from app.ml.enrichment import WINDOWS, _release_year, add_dynamics, price_features
from app.ml.news_history import aggregate_file
from tests.conftest import FIXTURES


def test_gdelt_v1_aggregation_counts_each_event_once_for_world(engine):
    from app.ml.news_history import _maps

    cameo, fips = _maps()
    df = aggregate_file(str(FIXTURES / "gdelt_v1_1989_sample.zip"), date(1989, 1, 1), cameo, fips, daily=False)
    world = df[df.iso3 == "WLD"]
    assert world["events"].sum() == 2000
    assert (df.loc[df.iso3 != "WLD", "events"] <= world["events"].max()).all()
    assert (world["material_conflict"] + world["verbal_conflict"] + world["verbal_cooperation"]
            + world["material_cooperation"]).sum() <= 2000
    assert set(df["month"].dt.year) == {1989}


def test_release_year_lag_rules():
    months = pd.Series(pd.to_datetime(["2020-03-01", "2020-04-01", "2020-06-01", "2020-07-01"]))
    assert list(_release_year(months, 4)) == [2018, 2019, 2019, 2019]  # V-Dem: Y-1 from April
    assert list(_release_year(months, 7)) == [2018, 2018, 2018, 2019]  # WDI: Y-1 from July


def test_prices_use_previous_month_only(tmp_path, monkeypatch):
    import app.ml.enrichment as en

    months = pd.date_range("2019-01-01", "2019-12-01", freq="MS")
    for sid in ("MCOILBRENTEU", "PFOODINDEXM", "PWHEAMTUSDM"):
        pd.DataFrame({"observation_date": months.strftime("%Y-%m-%d"), sid: np.arange(1, 13) * 10.0}).to_csv(
            tmp_path / f"fred_{sid}.csv", index=False)
    monkeypatch.setattr(en, "FACTOR_DIR", tmp_path)
    frame = pd.DataFrame({"iso3": "AAA", "month": months})
    out = price_features(frame).set_index("month")
    assert out.loc["2019-05-01", "brent_usd"] == 40.0  # April's value, never May's
    assert out.loc["2019-05-01", "brent_chg_3m_pct"] == (40.0 / 10.0 - 1) * 100


def test_dynamics_are_changes_of_lagged_values_without_leakage():
    months = pd.date_range("2020-01-01", periods=10, freq="MS")
    df = pd.DataFrame({"iso3": "AAA", "month": months, "news_violent_share": np.arange(10, dtype=float),
                       "wdi_inflation_pct": [5.0] * 6 + [9.0] * 4})
    out = add_dynamics(df).set_index("month")
    for w in WINDOWS:
        # value at row t minus value at row t-w, nothing from later rows
        assert out.loc["2020-08-01", f"news_violent_share_chg_{w}m"] == w
    assert np.isnan(out.loc["2020-01-01", "news_violent_share_chg_1m"])
    assert np.isnan(out.loc["2020-10-01", "wdi_inflation_pct_chg_12m"])  # needs 12 months of history
    # changing a future value must not change any earlier feature
    df2 = df.copy()
    df2.loc[9, "news_violent_share"] = 999.0
    out2 = add_dynamics(df2).set_index("month")
    cols = [c for c in out.columns if c.endswith("m")]
    pd.testing.assert_frame_equal(out.loc[:"2020-09-01", cols], out2.loc[:"2020-09-01", cols])


def test_politics_features_with_files_and_without(engine, tmp_path, monkeypatch):
    """Regression: the EPR block must run end to end; missing files must give empty columns, not errors."""
    import app.ml.enrichment as en
    from app.core.db import SessionLocal

    frame = pd.DataFrame({"iso3": ["UKR", "UKR"], "month": pd.to_datetime(["2020-03-01", "2020-06-01"])})
    monkeypatch.setattr(en, "FACTOR_DIR", tmp_path)
    with SessionLocal() as s:
        empty = en.politics_features(s, frame)
        assert list(empty.columns) == ["iso3", "month", *en.GROUPS["politics"]] and empty.iloc[:, 2:].isna().all().all()
        for slug, col in (("electoral-democracy-index", "electdem_vdem__estimate_best"),
                          ("liberal-democracy-index", "libdem_vdem__estimate_best")):
            pd.DataFrame({"entity": "Ukraine", "code": "UKR", "year": [2018, 2019], col: [0.4, 0.5]}).to_csv(
                tmp_path / f"owid_{slug}.csv", index=False)
        pd.DataFrame({"entity": "Ukraine", "code": "UKR", "year": [2018, 2019],
                      "regime_row_owid": ["Electoral autocracy", "Electoral democracy"]}).to_csv(
            tmp_path / "owid_political-regime.csv", index=False)
        pd.DataFrame({"gwid": [369, 369], "statename": "Ukraine", "from": [1991, 1991], "to": [2021, 2021],
                      "group": ["A", "B"], "size": [0.7, 0.2], "status": ["SENIOR PARTNER", "POWERLESS"]}).to_csv(
            tmp_path / "epr.csv", index=False)
        out = en.politics_features(s, frame).set_index("month")
    june = out.loc["2020-06-01"]  # V-Dem: year 2019 is published by April 2020
    assert june["vdem_electoral_democracy"] == 0.5 and june["vdem_regime_type"] == 2
    assert out.loc["2020-03-01", "vdem_electoral_democracy"] == 0.4  # before April -> 2018
    assert june["epr_relevant_groups"] == 2 and june["epr_excluded_groups"] == 1
    assert abs(june["epr_excluded_pop_share"] - 0.2) < 1e-9
