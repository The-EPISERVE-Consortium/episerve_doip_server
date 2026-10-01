"""Manifest cache expiry: FDO changes (e.g. accessRights) must take effect without a restart."""

import pytest

from doip_server import handlers, object_registry, protocol, storage_lakefs


class Clock:
    def __init__(self):
        self.now = 1000.0

    def __call__(self):
        return self.now


@pytest.fixture
def storage(monkeypatch):
    """Fake lakeFS manifest store: tests change ``state["profile"]`` and count fetches."""
    state = {"profile": {}, "fetches": 0}

    async def fake_get_fdo_metadata(qid):
        state["fetches"] += 1
        return {"kernel": {"fdo:hasComponent": []}, "profile": dict(state["profile"])}, "repo"

    monkeypatch.setattr(storage_lakefs, "get_fdo_metadata", fake_get_fdo_metadata)
    return state


@pytest.mark.asyncio
async def test_manifest_is_cached_within_ttl(storage):
    clock = Clock()
    registry = object_registry.ObjectRegistry(ttl=300, clock=clock)
    await registry.fetch_fdo_object("Q1")
    clock.now += 299
    await registry.fetch_fdo_object("Q1")
    assert storage["fetches"] == 1


@pytest.mark.asyncio
async def test_manifest_is_refetched_after_ttl(storage):
    clock = Clock()
    registry = object_registry.ObjectRegistry(ttl=300, clock=clock)
    await registry.fetch_fdo_object("Q1")
    clock.now += 301
    await registry.fetch_fdo_object("Q1")
    assert storage["fetches"] == 2


@pytest.mark.asyncio
async def test_ttl_zero_never_expires(storage):
    clock = Clock()
    registry = object_registry.ObjectRegistry(ttl=0, clock=clock)
    await registry.fetch_fdo_object("Q1")
    clock.now += 10_000_000
    await registry.fetch_fdo_object("Q1")
    assert storage["fetches"] == 1


@pytest.mark.asyncio
async def test_purge_still_evicts_immediately(storage):
    registry = object_registry.ObjectRegistry(ttl=300, clock=Clock())
    await registry.fetch_fdo_object("Q1")
    await registry.purge("Q1")
    await registry.fetch_fdo_object("Q1")
    assert storage["fetches"] == 2


@pytest.mark.asyncio
async def test_object_restricted_after_caching_is_denied_once_the_entry_expires(storage, monkeypatch):
    """The scenario that motivated the TTL: flag set after the manifest was cached."""
    monkeypatch.setattr(handlers.storage_lakefs, "get_read_token", lambda: "s3cret")
    clock = Clock()
    registry = object_registry.ObjectRegistry(ttl=300, clock=clock)
    request = protocol.DOIPMessage(
        version=protocol.DOIP_VERSION, msg_type=protocol.MSG_TYPE_REQUEST, operation=protocol.OP_RETRIEVE,
        flags=0, object_id="Q1", metadata_blocks=[{"element": "data.parquet"}],
    )

    await handlers._require_read_access("Q1", request, registry, element="data.parquet")  # public, now cached

    storage["profile"] = {"accessRights": "restricted"}
    await handlers._require_read_access("Q1", request, registry, element="data.parquet")  # still cached: allowed

    clock.now += 301
    with pytest.raises(protocol.AccessDeniedError):
        await handlers._require_read_access("Q1", request, registry, element="data.parquet")


@pytest.mark.asyncio
async def test_refetch_failure_after_expiry_fails_closed(storage, monkeypatch):
    clock = Clock()
    registry = object_registry.ObjectRegistry(ttl=300, clock=clock)
    await registry.fetch_fdo_object("Q1")
    clock.now += 301

    async def broken(qid):
        raise KeyError("lakeFS down")

    monkeypatch.setattr(storage_lakefs, "get_fdo_metadata", broken)
    with pytest.raises(KeyError):
        await registry.fetch_fdo_object("Q1")  # stale copy is not served


def test_ttl_from_environment(monkeypatch):
    for raw, expected in [(None, 300.0), ("", 300.0), ("45", 45.0), ("0", 0.0), ("-5", 0.0), ("soon", 300.0)]:
        if raw is None:
            monkeypatch.delenv("DOIP_MANIFEST_CACHE_TTL", raising=False)
        else:
            monkeypatch.setenv("DOIP_MANIFEST_CACHE_TTL", raw)
        assert object_registry.ObjectRegistry()._ttl == expected
