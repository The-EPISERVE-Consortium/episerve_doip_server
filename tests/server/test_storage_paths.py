import pytest

from doip_server import storage_lakefs


def test_build_object_key_sharded_with_branch(monkeypatch):
    storage_lakefs.configure({"lakefs": {"branch": "dev"}})
    key = storage_lakefs.build_object_key("Q12345", "primary.pdf")
    assert key == "dev/01/23/45/Q12345/components/primary.pdf"


@pytest.mark.asyncio
async def test_get_component_bytes_uses_sharded_path(monkeypatch):
    calls = {}

    async def fake_to_thread(func, *args, **kwargs):
        return func(*args, **kwargs)

    class FakeClient:
        def get_object(self, Bucket=None, Key=None):
            calls["bucket"] = Bucket
            calls["key"] = Key

            class Body:
                def read(self_inner):
                    return b"data"

            return {"Body": Body()}

        def get_paginator(self, *_args, **_kwargs):
            raise AssertionError("Paginator should not be called in this test")

    monkeypatch.setattr(storage_lakefs, "_client", lambda: FakeClient())
    monkeypatch.setattr(storage_lakefs.asyncio, "to_thread", fake_to_thread)
    storage_lakefs.configure({"lakefs": {"repos": ["repo-name"], "branch": "main"}})

    data = await storage_lakefs.get_component_bytes("Q4", "fulltext.pdf", "repo-name")

    assert data == b"data"
    assert calls["bucket"] == "repo-name"
    assert calls["key"] == "main/00/00/04/Q4/components/fulltext.pdf"


def test_build_component_object_path_uses_sharded_path():
    path = storage_lakefs.build_component_object_path("Q4", "fulltext.pdf")
    assert path == "00/00/04/Q4/components/fulltext.pdf"


@pytest.mark.asyncio
async def test_put_component_bytes_uses_lakefs_sdk_upload(monkeypatch):
    calls = {}

    async def fake_to_thread(func, *args, **kwargs):
        return func(*args, **kwargs)

    class FakeObject:
        def upload(self, data, mode="wb", content_type=None):
            calls["upload"] = {
                "data": data,
                "mode": mode,
                "content_type": content_type,
            }

    class FakeBranch:
        def object(self, path):
            calls["path"] = path
            return FakeObject()

    monkeypatch.setattr(storage_lakefs, "_lakefs_branch", lambda branch=None, repo=None: FakeBranch())
    monkeypatch.setattr(storage_lakefs.asyncio, "to_thread", fake_to_thread)
    storage_lakefs.configure({"lakefs": {"repos": ["repo-name"], "branch": "main"}})

    key = await storage_lakefs.put_component_bytes("Q4", "fulltext.pdf", b"data", repo="repo-name", media_type="application/pdf")

    assert calls["path"] == "00/00/04/Q4/components/fulltext.pdf"
    assert calls["upload"] == {
        "data": b"data",
        "mode": "wb",
        "content_type": "application/pdf",
    }
    assert key == "main/00/00/04/Q4/components/fulltext.pdf"


@pytest.mark.asyncio
async def test_commit_changes_uses_lakefs_sdk_branch(monkeypatch):
    calls = {}

    async def fake_to_thread(func, *args, **kwargs):
        return func(*args, **kwargs)

    class FakeRef:
        id = "commit-123"

    class FakeBranch:
        def commit(self, message=None, metadata=None, allow_empty=None):
            calls["commit"] = {
                "message": message,
                "metadata": metadata,
                "allow_empty": allow_empty,
            }
            return FakeRef()

    monkeypatch.setattr(storage_lakefs, "_lakefs_branch", lambda branch=None, repo=None: FakeBranch())
    monkeypatch.setattr(storage_lakefs.asyncio, "to_thread", fake_to_thread)
    storage_lakefs.configure({"lakefs": {"repos": ["repo-name"], "branch": "main"}})

    result = await storage_lakefs.commit_changes("test message", repo="repo-name", metadata={"op": "update"})

    assert calls["commit"]["message"] == "test message"
    assert calls["commit"]["metadata"] == {"op": "update"}
    assert calls["commit"]["allow_empty"] is True
    assert result == {"repo": "repo-name", "branch": "main", "commit_id": "commit-123"}


@pytest.mark.asyncio
async def test_reset_uncommitted_object_uses_lakefs_sdk_branch(monkeypatch):
    calls = {}

    async def fake_to_thread(func, *args, **kwargs):
        return func(*args, **kwargs)

    class FakeBranch:
        def reset_changes(self, path_type=None, path=None):
            calls["reset"] = {"path_type": path_type, "path": path}

    monkeypatch.setattr(storage_lakefs, "_lakefs_branch", lambda branch=None, repo=None: FakeBranch())
    monkeypatch.setattr(storage_lakefs.asyncio, "to_thread", fake_to_thread)

    await storage_lakefs.reset_uncommitted_object("00/00/04/Q4/components/fulltext.pdf", repo="repo-name")

    assert calls["reset"] == {
        "path_type": "object",
        "path": "00/00/04/Q4/components/fulltext.pdf",
    }
