import asyncio
import contextlib
from functools import partial
import logging

import pytest

from doip_client import StrictDOIPClient
from doip_server import handlers, main, protocol, storage_lakefs

logger = logging.getLogger(__name__)


class StubRegistry:
    def __init__(self):
        self.components = []

    async def fetch_fdo_object(self, pid):
        return {
            "@id": pid,
            "kernel": {"fdo:hasComponent": list(self.components)},
        }

    async def fetch_bitstream_bytes(self, pid):
        return b"hello-bytes"

    async def get_component(self, object_id, component_id, version=None):
        for component in self.components:
            if component.get("componentId") == component_id:
                content = await storage_lakefs.get_component_bytes(object_id, component_id, "test-repo")
                return content, component.get("mediaType", "application/octet-stream")
        raise KeyError(component_id)

    async def get_repo(self, pid):
        return "test-repo"

    async def purge(self, pid):
        return None


@pytest.mark.asyncio
async def test_client_server_integration_hello_and_retrieve(monkeypatch):
    async def fake_ensure():
        return True

    monkeypatch.setattr(storage_lakefs, "ensure_lakefs_available", fake_ensure)
    monkeypatch.setattr(storage_lakefs, "get_component_bytes", lambda *_, **__: b"hello-bytes")

    registry = StubRegistry()

    server = await asyncio.start_server(
        partial(main.handle_connection, registry), host="127.0.0.1", port=0
    )
    if not server.sockets:
        pytest.skip("no sockets")

    port = server.sockets[0].getsockname()[1]
    server_task = asyncio.create_task(server.serve_forever())

    try:
        client = StrictDOIPClient(host="127.0.0.1", port=port, use_tls=False, verify_tls=False)

        hello = await asyncio.to_thread(client.hello)
        assert hello.get("operation") == "hello"

        resp_meta = await asyncio.to_thread(client.retrieve, "Q123")
        assert resp_meta.header.op_code == protocol.OP_RETRIEVE
        assert resp_meta.metadata_blocks
        assert not resp_meta.component_blocks

    finally:
        server.close()
        await server.wait_closed()
        server_task.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await server_task


@pytest.mark.asyncio
async def test_client_server_integration_update_and_retrieve_component(monkeypatch):
    """Ensure authenticated updates succeed end to end and remain retrievable.

    Args:
        monkeypatch: Pytest monkeypatch fixture.

    Returns:
        None
    """
    stored = {}

    async def fake_put_component_bytes(object_id, component_id, data, repo, media_type="application/octet-stream"):
        stored[(object_id, component_id)] = (data, media_type)
        return "main/00/01/23/Q123/components/primary.pdf"

    async def fake_commit_changes(message, repo, metadata=None, branch=None, allow_empty=True):
        return {"repo": repo, "branch": "main", "commit_id": "commit-123"}

    async def fake_reset_uncommitted_object(object_path, repo, branch=None):
        raise AssertionError("reset should not be called in successful update test")

    async def fake_get_component_bytes(object_id, component_id, repo):
        return stored[(object_id, component_id)][0]

    monkeypatch.setattr(storage_lakefs, "get_update_token", lambda: "secret")
    monkeypatch.setattr(storage_lakefs, "put_component_bytes", fake_put_component_bytes)
    monkeypatch.setattr(storage_lakefs, "commit_changes", fake_commit_changes)
    monkeypatch.setattr(storage_lakefs, "reset_uncommitted_object", fake_reset_uncommitted_object)
    monkeypatch.setattr(storage_lakefs, "get_component_bytes", fake_get_component_bytes)

    registry = StubRegistry()

    server = await asyncio.start_server(
        partial(main.handle_connection, registry), host="127.0.0.1", port=0
    )
    if not server.sockets:
        pytest.skip("no sockets")

    port = server.sockets[0].getsockname()[1]
    server_task = asyncio.create_task(server.serve_forever())

    try:
        client = StrictDOIPClient(host="127.0.0.1", port=port, use_tls=False, verify_tls=False)

        update = await asyncio.to_thread(
            client.update_component,
            "Q123",
            "primary",
            b"updated-pdf",
            "application/pdf",
            "secret",
        )
        assert update.header.op_code == protocol.OP_UPDATE
        assert update.metadata_blocks[0]["status"] == "committed"

        registry.components = [{"componentId": "primary", "mediaType": "application/pdf"}]

        retrieved = await asyncio.to_thread(client.retrieve_component, "Q123", "primary")
        assert retrieved.header.op_code == protocol.OP_RETRIEVE
        assert retrieved.component_blocks[0].content == b"updated-pdf"
        assert retrieved.component_blocks[0].media_type == "application/pdf"

    finally:
        server.close()
        await server.wait_closed()
        server_task.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await server_task


@pytest.mark.asyncio
async def test_client_server_integration_update_rejects_invalid_token(monkeypatch):
    """Ensure invalid update tokens are rejected before any storage mutation.

    Args:
        monkeypatch: Pytest monkeypatch fixture.

    Returns:
        None
    """
    stored = {}

    async def fake_put_component_bytes(object_id, component_id, data, repo, media_type="application/octet-stream"):
        stored[(object_id, component_id)] = (data, media_type)
        raise AssertionError("put_component_bytes should not be called when auth fails")

    async def fake_commit_changes(message, repo, metadata=None, branch=None, allow_empty=True):
        raise AssertionError("commit_changes should not be called when auth fails")

    monkeypatch.setattr(storage_lakefs, "get_update_token", lambda: "secret")
    monkeypatch.setattr(storage_lakefs, "put_component_bytes", fake_put_component_bytes)
    monkeypatch.setattr(storage_lakefs, "commit_changes", fake_commit_changes)

    registry = StubRegistry()

    server = await asyncio.start_server(
        partial(main.handle_connection, registry), host="127.0.0.1", port=0
    )
    if not server.sockets:
        pytest.skip("no sockets")

    port = server.sockets[0].getsockname()[1]
    server_task = asyncio.create_task(server.serve_forever())

    try:
        client = StrictDOIPClient(host="127.0.0.1", port=port, use_tls=False, verify_tls=False)

        update = await asyncio.to_thread(
            client.update_component,
            "Q123",
            "primary",
            b"updated-pdf",
            "application/pdf",
            "wrong",
        )
        assert update.header.op_code == protocol.OP_UPDATE
        assert update.header.msg_type == protocol.MSG_TYPE_ERROR
        assert update.metadata_blocks[0]["message"] == "update authorization failed"
        assert stored == {}

    finally:
        server.close()
        await server.wait_closed()
        server_task.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await server_task
