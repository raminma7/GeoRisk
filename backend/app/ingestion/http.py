"""HTTP fetching with retries, exponential backoff and per-host rate limiting."""

from __future__ import annotations

import threading
import time
from urllib.parse import urlparse

import httpx
from tenacity import retry, retry_if_exception, stop_after_attempt, wait_exponential_jitter

from app.core.config import get_settings
from app.core.logging import get_logger

log = get_logger(__name__)


class NotFoundError(Exception):
    """Upstream resource does not exist (HTTP 404). Not retried."""


class RateLimiter:
    """Minimum interval between requests per host (process-local).

    Celery task locks (see workers.locks) prevent concurrent runs of the same
    connector across processes, so process-local limiting is sufficient.
    """

    def __init__(self) -> None:
        self._last: dict[str, float] = {}
        self._lock = threading.Lock()

    def wait(self, host: str, min_interval: float) -> None:
        if min_interval <= 0:
            return
        with self._lock:
            now = time.monotonic()
            last = self._last.get(host, 0.0)
            delay = last + min_interval - now
            if delay > 0:
                time.sleep(delay)
            self._last[host] = time.monotonic()


rate_limiter = RateLimiter()


def _retryable(exc: BaseException) -> bool:
    if isinstance(exc, NotFoundError):
        return False
    if isinstance(exc, httpx.HTTPStatusError):
        return exc.response.status_code in (408, 425, 429, 500, 502, 503, 504)
    return isinstance(exc, (httpx.TransportError, httpx.TimeoutException, RateLimitedError))


class RateLimitedError(Exception):
    pass


def _client() -> httpx.Client:
    s = get_settings()
    return httpx.Client(
        timeout=s.http_timeout_seconds,
        follow_redirects=True,
        headers={"User-Agent": "GeoRisk/0.1 (geopolitical monitoring research platform)"},
    )


def fetch(url: str, *, params: dict | None = None, min_interval: float = 0.0) -> httpx.Response:
    settings = get_settings()

    @retry(
        reraise=True,
        stop=stop_after_attempt(settings.http_max_retries),
        wait=wait_exponential_jitter(initial=2, max=60),
        retry=retry_if_exception(_retryable),
    )
    def _do() -> httpx.Response:
        rate_limiter.wait(urlparse(url).netloc, min_interval)
        with _client() as client:
            resp = client.get(url, params=params)
        if resp.status_code == 404:
            raise NotFoundError(url)
        resp.raise_for_status()
        # GDELT DOC answers rate-limit violations with HTTP 200 + plain-text notice
        if resp.headers.get("content-type", "").startswith("text/") and "limit requests" in resp.text[:200]:
            log.warning("upstream_rate_limited", url=url)
            raise RateLimitedError(url)
        return resp

    return _do()


def fetch_bytes(url: str, min_interval: float = 0.0) -> bytes:
    return fetch(url, min_interval=min_interval).content
