"""Rate limiting (slowapi). Uses Redis when reachable, memory otherwise."""

from __future__ import annotations

from slowapi import Limiter
from slowapi.util import get_remote_address

from app.core.config import get_settings


def _storage() -> str:
    s = get_settings()
    if s.environment == "test":
        return "memory://"
    return s.redis_url


limiter = Limiter(key_func=get_remote_address, default_limits=[get_settings().rate_limit_default],
                  storage_uri=_storage(), in_memory_fallback_enabled=True, swallow_errors=True)
