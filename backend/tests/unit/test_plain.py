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


def side(kind=None, iso3=None, country=None, igo=None):
    return {"kind": kind, "iso3": iso3, "country": country, "igo": igo}


def event(a, b, place="Tehran, Iran", iso3="IRN", country="Iran"):
    return {"a": a, "b": b, "place": place, "place_iso3": iso3, "place_country": country}


def test_capitals_and_cities_are_named_by_their_country():
    # GDELT codes "TEHRAN" as an actor with nationality IRN; we never print the raw "Tehran".
    cl = SimpleNamespace(event_root_code="19", quad_class=4)
    d = event(side("country", "IRN", "Iran"), side("country", "KWT", "Kuwait"))
    assert plain.incident_sentence(cl, d) == "Fighting in Tehran, Iran — Iran against Kuwait"
    same = event(side("country", "IRN", "Iran"), side("country", "IRN", "Iran"))  # "Iran against Tehran"
    assert plain.incident_sentence(cl, same) == "Fighting in Tehran, Iran"


def test_same_country_sides_read_as_internal_roles():
    cl = SimpleNamespace(event_root_code="19", quad_class=4)
    d = event(side("government", None, None), side("military", "IRN", "Iran"))
    assert plain.incident_sentence(cl, d) == "Fighting in Tehran, Iran — the government against the military"
    war = event(side("country", "RUS", "Russia"), side("country", "UKR", "Ukraine"),
                place="Okhrimivka, Ukraine", iso3="UKR", country="Ukraine")
    assert plain.incident_sentence(cl, war) == "Fighting in Okhrimivka, Ukraine — Russia against Ukraine"
    us = event(side("military", "USA", "United States"), side("country", "CHN", "China"), place="China", iso3="CHN",
               country="China")
    assert plain.incident_sentence(cl, us) == "Fighting in China — the military of the United States against China"


def test_vague_or_implausible_actors_are_left_out():
    attack = SimpleNamespace(event_root_code="18", quad_class=4)
    d = event(side("judiciary", None, None), side("media", None, None), place="Kerala, India", iso3="IND", country="India")
    assert plain.incident_sentence(attack, d) == "Attack in Kerala, India"  # "attack by the courts" is a misreading
    nowhere = event(side("business"), side(), place=None, iso3=None, country=None)
    assert plain.incident_sentence(attack, nowhere) is None
    talks = SimpleNamespace(event_root_code="16", quad_class=3)
    assert plain.incident_sentence(talks, None) is None


def test_place_is_shortened():
    assert plain.short_place("Kyiv, Kyyiv, Misto, Ukraine", "Ukraine") == "Kyiv, Ukraine"
    assert plain.short_place("Bangladesh (general), Bangladesh", "Bangladesh") == "Bangladesh"
    assert plain.the("United States") == "the United States" and plain.the("Iran") == "Iran"


def test_news_alone_cannot_make_a_country_critical():
    # severity = verified deaths, raised by at most one step by today's news
    assert plain.combined_level("critical", "low") == "elevated"      # e.g. United States, China: lots of news, no war
    assert plain.combined_level("critical", "elevated") == "high"     # ~100+ deaths
    assert plain.combined_level("critical", "high") == "critical"     # ~1,000+ deaths and heavy news
    assert plain.combined_level("low", "critical") == "critical"      # a war stays critical on a quiet news day
    assert plain.combined_level("elevated", "low") == "elevated"
