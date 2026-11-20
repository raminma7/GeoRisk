"""Crisis index: five dimensions, not deaths alone."""

import numpy as np
import pandas as pd

from app.ml import crisis_index as CI


def test_deaths_and_momentum_scales():
    assert list(CI.deaths_score([0, 9, 99, 999, 9_999, 90_000]).round()) == [0, 25, 50, 75, 100, 100]
    assert list(CI.momentum_score([1.0, 2.0, 0.5, 4.0])) == [50, 100, 0, 100]


def test_little_news_is_pulled_towards_the_world_average():
    df = pd.DataFrame({"ev": [400, 40_000], "v": [0, 0], "mc": [0, 0], "th": [200, 20_000], "dt": [0, 0], "pr": [0, 0]})
    sh = CI.shares(df)
    assert sh.loc[1, "tension"] > 0.47 and sh.loc[0, "tension"] < 0.2  # 50% tension: trusted only with enough news
    assert np.isnan(CI.shares(pd.DataFrame({"ev": [100], "v": [0], "mc": [0], "th": [0], "dt": [0], "pr": [0]}))
                    .loc[0, "tension"])  # below 300 events: no news dimensions


def test_strongest_dimension_counts_and_calm_ones_do_not_pull_down():
    dims = pd.DataFrame([
        {"violence": 98, "military": 5, "tension": 60, "unrest": 0, "momentum": 40},    # all-out war, little else
        {"violence": 5, "military": 10, "tension": 99, "unrest": 10, "momentum": 50},   # tense stand-off, no deaths
        {"violence": 20, "military": 40, "tension": 45, "unrest": 30, "momentum": 50},  # ordinary country
    ])
    s = CI.compose(dims)
    assert s[0] > s[1] > s[2]
    assert s[0] >= 98 and s[1] >= 74  # one extreme dimension is enough to be serious
    only_violence = CI.compose(pd.DataFrame([{"violence": 60, "military": np.nan, "tension": np.nan, "unrest": np.nan,
                                              "momentum": np.nan}]))
    assert only_violence[0] >= 60  # no news: score from deaths only


def test_news_never_lowers_verified_deaths():
    ref = {k: np.linspace(0, 0.3, 1001) for k in ("violent", "military", "tension", "unrest")}
    sh = pd.DataFrame({"violent": [0.0, 0.3], "military": [0.1, 0.1], "tension": [0.1, 0.1], "unrest": [0.0, 0.0]})
    d = CI.dimension_frame(sh, [10_000, 0], [1.0, 1.0], ref)
    assert d.loc[0, "violence"] == 100      # quiet news does not lower 10,000 verified deaths
    assert d.loc[1, "violence"] == 40       # news alone can raise violence, but only part of the way


def test_levels_from_cut_offs():
    cuts = {"critical": 90.0, "high": 80.0, "elevated": 70.0}
    assert [CI.level_of(v, cuts) for v in (95, 85, 72, 10, None)] == ["critical", "high", "elevated", "low", "low"]
