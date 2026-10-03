"""Tests for the rate-limit stores (memory + Redis backend)."""
import pytest

from app import config as config_module
from app.core import limit_stores
from app.core.limit_stores import MemoryStore, RedisStore, get_store


class FakeRedis:
    """Minimal sorted-set surface used by RedisStore."""

    def __init__(self):
        self.data = {}

    def zremrangebyscore(self, key, min_score, max_score):
        members = self.data.get(key, {})
        for member in [m for m, s in members.items() if s <= max_score]:
            del members[member]
        return 0

    def zcard(self, key):
        return len(self.data.get(key, {}))

    def zadd(self, key, mapping):
        self.data.setdefault(key, {}).update(mapping)
        return 1

    def expire(self, key, ttl):
        return True


def test_memory_store_allows_then_blocks():
    store = MemoryStore()
    assert store.hit("k", window=60, limit=2)
    assert store.hit("k", window=60, limit=2)
    assert not store.hit("k", window=60, limit=2)
    store.reset()
    assert store.hit("k", window=60, limit=2)


def test_memory_store_window_expiry():
    import time

    store = MemoryStore()
    assert store.hit("k", window=0.05, limit=1)
    assert not store.hit("k", window=0.05, limit=1)
    time.sleep(0.06)
    assert store.hit("k", window=0.05, limit=1)


def test_redis_store_allows_then_blocks():
    store = RedisStore(FakeRedis())
    assert store.hit("k", window=60, limit=2)
    assert store.hit("k", window=60, limit=2)
    assert not store.hit("k", window=60, limit=2)


def test_redis_store_failure_fails_open():
    class Broken:
        def zremrangebyscore(self, *a):
            raise ConnectionError("down")

    assert RedisStore(Broken()).hit("k", window=60, limit=1) is True


def test_factory_defaults_to_memory(monkeypatch):
    monkeypatch.setattr(config_module.settings, "redis_url", "")
    limit_stores.reset_rate_limit_store()
    try:
        assert isinstance(get_store(), MemoryStore)
    finally:
        limit_stores.reset_rate_limit_store()


def test_factory_uses_redis_when_configured(monkeypatch):
    import redis

    monkeypatch.setattr(config_module.settings, "redis_url",
                        "redis://localhost:6379/0")
    monkeypatch.setattr(redis.Redis, "from_url",
                        classmethod(lambda cls, *a, **k: FakeRedis()))
    limit_stores.reset_rate_limit_store()
    try:
        store = get_store()
        assert isinstance(store, RedisStore)
        assert store.hit("k", window=60, limit=1)
    finally:
        limit_stores.reset_rate_limit_store()
        monkeypatch.setattr(config_module.settings, "redis_url", "")


def test_middleware_uses_redis_store(client, monkeypatch):
    import redis

    monkeypatch.setattr(config_module.settings, "redis_url",
                        "redis://localhost:6379/0")
    fake = FakeRedis()
    monkeypatch.setattr(redis.Redis, "from_url",
                        classmethod(lambda cls, *a, **k: fake))
    monkeypatch.setattr(config_module.settings, "rate_limit_enabled", True)
    monkeypatch.setattr(config_module.settings, "rate_limit_per_minute", 2)
    limit_stores.reset_rate_limit_store()
    try:
        assert client.get("/v1/courses").status_code == 200
        assert client.get("/v1/courses").status_code == 200
        assert client.get("/v1/courses").status_code == 429
        assert fake.data  # counters live in the shared store
    finally:
        limit_stores.reset_rate_limit_store()
        monkeypatch.setattr(config_module.settings, "redis_url", "")


@pytest.fixture(autouse=True)
def _restore_env():
    yield
    config_module.settings.redis_url = ""
    limit_stores.reset_rate_limit_store()
