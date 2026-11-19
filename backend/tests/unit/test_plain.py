"""Plain-language wording for non-specialist readers."""

from types import SimpleNamespace

from app.monitoring import plain


def crisis(**kw):
    base = {"location_country_iso3s": ["UKR"], "core_country_iso3s": ["RUS", "UKR"], "priority_level": "high",
            "direction": "stable", "current_indicators": {}}
    return SimpleNamespace(**{**base, **kw})


NAMES = {"UKR": "Ukraine", "RUS": "Russia", "USA": "United States", "CHN": "China", "GBR": "United Kingdom"}


def test_kind_and_headline_follow_verified_deaths():
    deaths = {"UKR": 50_000}
    c = crisis()
    assert plain._kind(c, deaths) == "conflict"
    assert plain.headline(c, "conflict", NAMES, deaths) == "Armed conflict in Ukraine, involving Russia"
    small = {"UKR": 40}
    assert plain.headline(c, "conflict", NAMES, small).startswith("Armed violence in Ukraine")
    t = crisis(location_country_iso3s=["CHN"], core_country_iso3s=["CHN", "USA"])
    assert plain._kind(t, {}) == "tension"
    assert plain.headline(t, "tension", NAMES, {}) == "Tensions between China and United States"
    d = crisis(location_country_iso3s=["GBR"], core_country_iso3s=["GBR"])
    assert plain._kind(d, {}) == "domestic"
    assert plain.headline(d, "domestic", NAMES, {}) == "Unrest and violence reports in United Kingdom"


def test_wording_helpers():
    assert plain.join_names(["A", "B", "C"]) == "A, B and C"
    assert plain.round_count(91_234) == "91,000" and plain.round_count(87) == "87" and plain.round_count(2_149) == "2,100"
    assert plain.deaths_level(12_000) == "critical" and plain.deaths_level(1_500) == "high" and plain.deaths_level(10) == "low"
    assert plain.confidence({"evidence_confidence_label": "high", "independent_sources_24h": 141})["label"] == "Well confirmed"


def test_incident_sentence_is_readable():
    cl = SimpleNamespace(event_root_code="19", quad_class=4)
    ev = SimpleNamespace(actor1_name="RUSSIAN", actor2_name="UKRAINIAN", action_location="Kharkiv, Ukraine")
    assert plain.incident_sentence(cl, ev) == "Fighting in Kharkiv, Ukraine — Russian against Ukrainian"
    talks = SimpleNamespace(event_root_code="16", quad_class=3)
    assert plain.incident_sentence(talks, None) == "Relations cut or sanctions"
