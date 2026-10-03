"""Rate limiting stores: in-memory sliding window + shared Redis backend.

Single process (dev, tests) uses MemoryStore. Set REDIS_URL to share
counters across workers/hosts. Redis uses a sorted-set sliding window
(check-then-add; a tiny race under concurrency is acceptable for a
limiter and documented). Redis outages fail OPEN (logged) so a cache
blip never takes the API down.
"""
import logging
import time

logger = logging.getLogger("talyn.ratelimit")


class MemoryStore:
    """Per-process sliding window."""

    def __init__(self) -> None:
        self._hits: dict[str, list[float]] = {}

    def hit(self, key: str, window: float, limit: int) -> bool:
        """Record a hit. True = allowed, False = over the limit."""
        now = time.monotonic()
        recent = [t for t in self._hits.get(key, []) if now - t < window]
        if len(recent) >= limit:
            self._hits[key] = recent
            return False
        recent.append(now)
        self._hits[key] = recent
        return True

    def reset(self) -> None:
        self._hits.clear()


class RedisStore:
    """Shared sliding window over a Redis sorted set."""

    def __init__(self, client) -> None:
        self._client = client

    def hit(self, key: str, window: float, limit: int) -> bool:
        now = time.time()
        try:
            self._client.zremrangebyscore(key, 0, now - window)
            if int(self._client.zcard(key)) >= limit:
                return False
            self._client.zadd(key, {f"{time.time_ns()}": now})
            self._client.expire(key, int(window) + 1)
            return True
        except Exception as e:
            logger.warning("rate limiter redis error, failing open: %s", e)
            return True


_store_instance = None


def get_store():
    """MemoryStore by default; RedisStore when REDIS_URL is configured."""
    global _store_instance
    if _store_instance is not None:
        return _store_instance
    from app import config as config_module

    url = config_module.settings.redis_url
    if url:
        import redis

        _store_instance = RedisStore(
            redis.Redis.from_url(url, socket_timeout=2)
        )
    else:
        _store_instance = MemoryStore()
    return _store_instance


def redis_available() -> bool:
    """True when the shared limiter store can actually be reached.

    The limiter deliberately fails open (an unreachable Redis must not lock
    every learner out), which means a dead Redis is invisible unless something
    checks. This is what /health/ready reports.
    """
    store = get_store()
    if not isinstance(store, RedisStore):
        return False
    try:
        return bool(store._client.ping())
    except Exception:
        return False


def reset_rate_limit_store() -> None:
    """Drop the store (tests). Next use rebuilds it from current settings."""
    global _store_instance
    if isinstance(_store_instance, MemoryStore):
        _store_instance.reset()
    _store_instance = None
