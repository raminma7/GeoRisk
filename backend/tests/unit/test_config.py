"""Settings must load from plain .env style values (regression: list fields crashed on startup)."""

from pathlib import Path

from app.core.config import Settings


def test_list_fields_accept_csv_json_and_empty(monkeypatch):
    monkeypatch.setenv("CORS_ORIGINS", "http://localhost:3000,http://example.org")
    monkeypatch.setenv("WATCHLIST", "ukr, RUS")
    s = Settings(_env_file=None)
    assert s.cors_origins == ["http://localhost:3000", "http://example.org"]
    assert s.watchlist == ["UKR", "RUS"]
    monkeypatch.setenv("CORS_ORIGINS", '["http://a"]')
    monkeypatch.setenv("WATCHLIST", '["isr"]')
    s = Settings(_env_file=None)
    assert s.cors_origins == ["http://a"] and s.watchlist == ["ISR"]


def test_env_example_loads(monkeypatch):
    example = Path(__file__).parents[3] / ".env.example"
    for line in example.read_text().splitlines():  # let the file, not the test harness env, provide values
        if "=" in line and not line.startswith("#"):
            monkeypatch.delenv(line.split("=", 1)[0], raising=False)
    s = Settings(_env_file=str(example))
    assert s.environment == "development"
    assert s.cors_origins == ["http://localhost:3000"]
    assert s.ai_provider == "none" and "UKR" in s.watchlist
