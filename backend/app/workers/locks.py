"""Redis-based task locks preventing duplicate job execution across workers."""

from __future__ import annotations

import functools
import uuid
from collections.abc import Callable

import redis

from app.core.config import get_settings
from app.core.logging import get_logger

log = get_logger(__name__)
_client: redis.Redis | None = None

RELEASE_SCRIPT = "if redis.call('get', KEYS[1]) == ARGV[1] then return redis.call('del', KEYS[1]) else return 0 end"


def redis_client() -> redis.Redis:
    global _client
    if _client is None:
        _client = redis.Redis.from_url(get_settings().redis_url, decode_responses=True)
    return _client


def single_instance(name: str, ttl_seconds: int = 3600) -> Callable:
    """Decorator: skip the run if another instance holds the lock."""

    def deco(fn: Callable) -> Callable:
        @functools.wraps(fn)
        def wrapper(*args, **kwargs):
            r = redis_client()
            key = f"georisk:lock:{name}"
            token = str(uuid.uuid4())
            if not r.set(key, token, nx=True, ex=ttl_seconds):
                log.info("task_skipped_locked", task=name)
                return {"skipped": "already running"}
            try:
                return fn(*args, **kwargs)
            finally:
                r.eval(RELEASE_SCRIPT, 1, key, token)

        return wrapper

    return deco
