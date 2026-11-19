"""Factor downloader: writes atomically, skips existing files, survives a failing source."""

from app.core.config import get_settings
from app.ml import factors


def test_download_skip_and_partial_failure(tmp_path, monkeypatch):
    monkeypatch.setattr(get_settings(), "factor_dir", str(tmp_path))
    calls = []

    def fetch(url):
        calls.append(url)
        if "icr.ethz.ch" in url:
            raise RuntimeError("host down")
        return b"x" * 100

    out = factors.download_factors(fetch=fetch)
    assert out["failed"] == ["epr.csv"]
    assert (tmp_path / "wdi_NY.GDP.PCAP.KD.json").stat().st_size == 100
    assert not list(tmp_path.glob("*.part"))
    assert factors.missing() == ["epr.csv"]
    n = len(calls)
    factors.download_factors(fetch=fetch)  # second run only retries the missing file
    assert len(calls) == n + 1
    factors.download_factors(fetch=fetch, refresh=True)
    assert len(calls) == n + 1 + len(factors.sources())
