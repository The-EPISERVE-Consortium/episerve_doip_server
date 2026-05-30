import asyncio
from pathlib import Path

import pytest
import yaml

from doip_server import handlers, object_registry, protocol, storage_lakefs


class StubRegistry(object_registry.ObjectRegistry):
    def __init__(self, components):
        """Initialize stub registry with predefined components.

        Args:
            components: Component entries to return for any request.
        """
        super().__init__()
        self._components = components

    async def get_components(self, qid):
        """Return stubbed components for any QID.

        Args:
            qid: Ignored object identifier.

        Returns:
            list[dict]: Predefined component metadata.
        """
        return self._components

    async def fetch_fdo_object(self, pid):
        """Return a minimal manifest to avoid network access during tests."""
        return {"kernel": {"fdo:hasComponent": self._components}}


@pytest.mark.asyncio
async def test_handle_hello_returns_capabilities():
    """Ensure hello handler returns basic status and operations metadata.

    Returns:
        None
    """
    registry = StubRegistry([])
    request = protocol.DOIPMessage(
        version=protocol.DOIP_VERSION,
        msg_type=protocol.MSG_TYPE_REQUEST,
        operation=protocol.OP_HELLO,
        flags=0,
        object_id="",
        metadata_blocks=[{"operation": "hello"}],
    )

    response = await handlers.handle_hello(request, registry)

    assert response.msg_type == protocol.MSG_TYPE_RESPONSE
    assert response.operation == protocol.OP_HELLO
    meta = response.metadata_blocks[0]
    assert meta["operation"] == "hello"
    assert meta["status"] == "ok"
    assert "availableOperations" in meta


@pytest.mark.asyncio
async def test_retrieve_metadata_for_qid(monkeypatch):
    registry = StubRegistry([])
    request = protocol.DOIPMessage(
        version=protocol.DOIP_VERSION,
        msg_type=protocol.MSG_TYPE_REQUEST,
        operation=protocol.OP_RETRIEVE,
        flags=0,
        object_id="Q123",
        metadata_blocks=[]
    )

    response = await handlers.handle_retrieve(request, registry)

    assert response.msg_type == protocol.MSG_TYPE_RESPONSE
    assert response.operation == protocol.OP_RETRIEVE
    assert len(response.metadata_blocks) == 1
    assert response.component_blocks == []


@pytest.mark.asyncio
async def test_retrieve_fdo_metadata(monkeypatch):
    async def fake_fetch_fdo(pid):
        return {"foo": "bar"}

    # Registry returns JSON-LD FDO metadata
    registry = StubRegistry([])
    registry.fetch_fdo_object = fake_fetch_fdo

    request = protocol.DOIPMessage(
        version=protocol.DOIP_VERSION,
        msg_type=protocol.MSG_TYPE_REQUEST,
        operation=protocol.OP_RETRIEVE,
        flags=0,
        object_id="Q123",
        metadata_blocks=[]
    )

    response = await handlers.handle_retrieve(request, registry)

    assert response.msg_type == protocol.MSG_TYPE_RESPONSE
    assert response.operation == protocol.OP_RETRIEVE

    # Metadata present, no binary components
    assert response.component_blocks == []
    assert response.metadata_blocks == [{"foo": "bar"}]


@pytest.mark.asyncio
async def test_retrieve_specific_component(monkeypatch):
    async def fake_ensure(): return True
    async def fake_get_bytes(qid, comp): return b"hello"
    async def fake_fetch_fdo(pid):
        return {
            "@graph": [
                {
                    "@id": "components/primary.pdf",
                    "@type": "File",
                    "name": "primary.pdf",
                    "encodingFormat": "application/pdf",
                }
            ]
        }

    monkeypatch.setattr(handlers.storage_lakefs, "ensure_lakefs_available", fake_ensure)
    monkeypatch.setattr(handlers.storage_lakefs, "get_component_bytes", fake_get_bytes)

    registry = StubRegistry([])
    registry.fetch_fdo_object = fake_fetch_fdo

    request = protocol.DOIPMessage(
        version=protocol.DOIP_VERSION,
        msg_type=protocol.MSG_TYPE_REQUEST,
        operation=protocol.OP_RETRIEVE,
        flags=0,
        object_id="Q123",
        metadata_blocks=[{"element": "primary.pdf"}],
    )

    response = await handlers.handle_retrieve(request, registry)

    assert response.msg_type == protocol.MSG_TYPE_RESPONSE
    assert response.operation == protocol.OP_RETRIEVE
    assert response.metadata_blocks == []

    assert len(response.component_blocks) == 1
    comp = response.component_blocks[0]
    assert comp.component_id == "primary.pdf"
    assert comp.content == b"hello"
    assert comp.media_type == "application/pdf"


@pytest.mark.asyncio
async def test_retrieve_component_defaults_when_manifest_missing(monkeypatch):
    """Component retrieval falls back to octet-stream when media type unknown."""

    async def fake_ensure():
        return True

    async def fake_get_bytes(qid, comp):
        return b"content"

    async def fake_fetch_fdo(pid):
        return {
            "@graph": [
                {
                    "@id": "components/primary",
                    "@type": "File",
                    "name": "primary",
                }
            ]
        }

    monkeypatch.setattr(handlers.storage_lakefs, "ensure_lakefs_available", fake_ensure)
    monkeypatch.setattr(handlers.storage_lakefs, "get_component_bytes", fake_get_bytes)

    registry = StubRegistry([])
    registry.fetch_fdo_object = fake_fetch_fdo

    request = protocol.DOIPMessage(
        version=protocol.DOIP_VERSION,
        msg_type=protocol.MSG_TYPE_REQUEST,
        operation=protocol.OP_RETRIEVE,
        flags=0,
        object_id="Q123",
        metadata_blocks=[{"element": "primary"}],
    )

    response = await handlers.handle_retrieve(request, registry)

    assert response.msg_type == protocol.MSG_TYPE_RESPONSE
    assert response.operation == protocol.OP_RETRIEVE
    assert response.metadata_blocks == []

    assert len(response.component_blocks) == 1
    comp = response.component_blocks[0]
    assert comp.component_id == "primary"
    assert comp.media_type == "application/octet-stream"
    assert comp.content == b"content"


@pytest.mark.asyncio
async def test_handle_update_stores_component_and_commits(monkeypatch):
    """Ensure authenticated updates write one component and commit it.

    Args:
        monkeypatch: Pytest monkeypatch fixture.

    Returns:
        None
    """
    calls = {}

    async def fake_fetch_fdo(pid):
        return {"@id": pid}

    async def fake_put_component_bytes(object_id, component_id, data, media_type="application/octet-stream"):
        calls["put"] = {
            "object_id": object_id,
            "component_id": component_id,
            "data": data,
            "media_type": media_type,
        }
        return "main/00/00/01/Q1/components/primary.pdf"

    async def fake_commit_changes(message, metadata=None, branch=None, allow_empty=True):
        calls["commit"] = {
            "message": message,
            "metadata": metadata,
            "branch": branch,
            "allow_empty": allow_empty,
        }
        return {"branch": "main", "commit_id": "abc123", "repo": "repo"}

    async def fake_reset_uncommitted_object(object_path, branch=None):
        calls["reset"] = {"object_path": object_path, "branch": branch}

    registry = StubRegistry([])
    registry.fetch_fdo_object = fake_fetch_fdo

    monkeypatch.setattr(handlers.storage_lakefs, "get_update_token", lambda: "secret")
    monkeypatch.setattr(handlers.storage_lakefs, "put_component_bytes", fake_put_component_bytes)
    monkeypatch.setattr(handlers.storage_lakefs, "commit_changes", fake_commit_changes)
    monkeypatch.setattr(handlers.storage_lakefs, "reset_uncommitted_object", fake_reset_uncommitted_object)

    request = protocol.DOIPMessage(
        version=protocol.DOIP_VERSION,
        msg_type=protocol.MSG_TYPE_REQUEST,
        operation=protocol.OP_UPDATE,
        flags=0,
        object_id="Q1",
        metadata_blocks=[{"operation": "update", "element": "primary.pdf", "token": "secret"}],
        component_blocks=[
            protocol.ComponentBlock(
                component_id="primary.pdf",
                content=b"pdf-data",
                media_type="application/pdf",
            )
        ],
    )

    response = await handlers.handle_update(request, registry)

    assert calls["put"]["object_id"] == "Q1"
    assert calls["put"]["component_id"] == "primary.pdf"
    assert calls["put"]["data"] == b"pdf-data"
    assert "commit" in calls
    assert "reset" not in calls
    assert response.operation == protocol.OP_UPDATE
    assert response.metadata_blocks[0]["status"] == "committed"
    assert response.metadata_blocks[0]["commitId"] == "abc123"


@pytest.mark.asyncio
async def test_handle_update_rejects_multiple_components(monkeypatch):
    """Ensure update rejects requests carrying more than one component block.

    Args:
        monkeypatch: Pytest monkeypatch fixture.

    Returns:
        None
    """
    monkeypatch.setattr(handlers.storage_lakefs, "get_update_token", lambda: "secret")
    registry = StubRegistry([])
    metadata = [{"operation": "update", "element": "primary", "token": "secret"}]
    request = protocol.DOIPMessage(
        version=protocol.DOIP_VERSION,
        msg_type=protocol.MSG_TYPE_REQUEST,
        operation=protocol.OP_UPDATE,
        flags=0,
        object_id="Q1",
        metadata_blocks=metadata,
        component_blocks=[
            protocol.ComponentBlock(component_id="primary", content=b"one"),
            protocol.ComponentBlock(component_id="secondary", content=b"two"),
        ],
    )

    with pytest.raises(protocol.ProtocolError):
        await handlers.handle_update(request, registry)


@pytest.mark.asyncio
async def test_handle_update_rejects_mismatched_component_id(monkeypatch):
    """Ensure update rejects mismatched metadata and component identifiers.

    Args:
        monkeypatch: Pytest monkeypatch fixture.

    Returns:
        None
    """
    async def fake_fetch_fdo(pid):
        return {"@id": pid}

    registry = StubRegistry([])
    registry.fetch_fdo_object = fake_fetch_fdo
    monkeypatch.setattr(handlers.storage_lakefs, "get_update_token", lambda: "secret")

    request = protocol.DOIPMessage(
        version=protocol.DOIP_VERSION,
        msg_type=protocol.MSG_TYPE_REQUEST,
        operation=protocol.OP_UPDATE,
        flags=0,
        object_id="Q1",
        metadata_blocks=[{"operation": "update", "element": "primary", "token": "secret"}],
        component_blocks=[protocol.ComponentBlock(component_id="secondary", content=b"data")],
    )

    with pytest.raises(protocol.ProtocolError):
        await handlers.handle_update(request, registry)


@pytest.mark.asyncio
async def test_handle_update_resets_uncommitted_object_on_commit_failure(monkeypatch):
    """Ensure update resets staged lakeFS changes when commit creation fails.

    Args:
        monkeypatch: Pytest monkeypatch fixture.

    Returns:
        None
    """
    calls = {}

    async def fake_fetch_fdo(pid):
        return {"@id": pid}

    async def fake_put_component_bytes(object_id, component_id, data, media_type="application/octet-stream"):
        calls["put"] = True
        return "main/00/00/01/Q1/components/primary"

    async def fake_commit_changes(message, metadata=None, branch=None, allow_empty=True):
        raise RuntimeError("commit failed")

    async def fake_reset_uncommitted_object(object_path, branch=None):
        calls["reset"] = {"object_path": object_path, "branch": branch}

    registry = StubRegistry([])
    registry.fetch_fdo_object = fake_fetch_fdo

    monkeypatch.setattr(handlers.storage_lakefs, "get_update_token", lambda: "secret")
    monkeypatch.setattr(handlers.storage_lakefs, "put_component_bytes", fake_put_component_bytes)
    monkeypatch.setattr(handlers.storage_lakefs, "commit_changes", fake_commit_changes)
    monkeypatch.setattr(handlers.storage_lakefs, "reset_uncommitted_object", fake_reset_uncommitted_object)

    request = protocol.DOIPMessage(
        version=protocol.DOIP_VERSION,
        msg_type=protocol.MSG_TYPE_REQUEST,
        operation=protocol.OP_UPDATE,
        flags=0,
        object_id="Q1",
        metadata_blocks=[{"operation": "update", "element": "primary", "token": "secret"}],
        component_blocks=[protocol.ComponentBlock(component_id="primary", content=b"data")],
    )

    with pytest.raises(RuntimeError):
        await handlers.handle_update(request, registry)

    assert calls["put"] is True
    assert calls["reset"]["object_path"] == "00/00/01/Q1/components/primary"


@pytest.mark.asyncio
async def test_handle_update_rejects_missing_token_before_fetch(monkeypatch):
    """Ensure update authorization happens before object lookup and storage writes.

    Args:
        monkeypatch: Pytest monkeypatch fixture.

    Returns:
        None
    """
    monkeypatch.setattr(handlers.storage_lakefs, "get_update_token", lambda: "secret")

    registry = StubRegistry([])

    async def fake_fetch_fdo(pid):
        raise AssertionError("fetch_fdo_object should not be called without auth")

    async def fake_put_component_bytes(*args, **kwargs):
        raise AssertionError("put_component_bytes should not be called without auth")

    async def fake_commit_changes(*args, **kwargs):
        raise AssertionError("commit_changes should not be called without auth")

    registry.fetch_fdo_object = fake_fetch_fdo
    monkeypatch.setattr(handlers.storage_lakefs, "put_component_bytes", fake_put_component_bytes)
    monkeypatch.setattr(handlers.storage_lakefs, "commit_changes", fake_commit_changes)

    request = protocol.DOIPMessage(
        version=protocol.DOIP_VERSION,
        msg_type=protocol.MSG_TYPE_REQUEST,
        operation=protocol.OP_UPDATE,
        flags=0,
        object_id="Q1",
        metadata_blocks=[{"operation": "update", "element": "primary"}],
        component_blocks=[protocol.ComponentBlock(component_id="primary", content=b"data")],
    )

    with pytest.raises(protocol.ProtocolError, match="update authorization failed"):
        await handlers.handle_update(request, registry)


@pytest.mark.asyncio
async def test_handle_update_rejects_invalid_token_before_storage(monkeypatch):
    """Ensure update rejects incorrect shared secrets before any side effects.

    Args:
        monkeypatch: Pytest monkeypatch fixture.

    Returns:
        None
    """
    monkeypatch.setattr(handlers.storage_lakefs, "get_update_token", lambda: "secret")

    registry = StubRegistry([])

    async def fake_fetch_fdo(pid):
        raise AssertionError("fetch_fdo_object should not be called with invalid auth")

    async def fake_put_component_bytes(*args, **kwargs):
        raise AssertionError("put_component_bytes should not be called with invalid auth")

    async def fake_commit_changes(*args, **kwargs):
        raise AssertionError("commit_changes should not be called with invalid auth")

    registry.fetch_fdo_object = fake_fetch_fdo
    monkeypatch.setattr(handlers.storage_lakefs, "put_component_bytes", fake_put_component_bytes)
    monkeypatch.setattr(handlers.storage_lakefs, "commit_changes", fake_commit_changes)

    request = protocol.DOIPMessage(
        version=protocol.DOIP_VERSION,
        msg_type=protocol.MSG_TYPE_REQUEST,
        operation=protocol.OP_UPDATE,
        flags=0,
        object_id="Q1",
        metadata_blocks=[{"operation": "update", "element": "primary", "token": "wrong"}],
        component_blocks=[protocol.ComponentBlock(component_id="primary", content=b"data")],
    )

    with pytest.raises(protocol.ProtocolError, match="update authorization failed"):
        await handlers.handle_update(request, registry)


@pytest.mark.asyncio
async def test_handle_update_rejects_when_server_token_is_unset(monkeypatch):
    """Ensure update rejects requests when the server lacks a configured token.

    Args:
        monkeypatch: Pytest monkeypatch fixture.

    Returns:
        None
    """
    monkeypatch.setattr(handlers.storage_lakefs, "get_update_token", lambda: None)

    registry = StubRegistry([])

    async def fake_fetch_fdo(pid):
        raise AssertionError("fetch_fdo_object should not run when auth is unconfigured")

    registry.fetch_fdo_object = fake_fetch_fdo

    request = protocol.DOIPMessage(
        version=protocol.DOIP_VERSION,
        msg_type=protocol.MSG_TYPE_REQUEST,
        operation=protocol.OP_UPDATE,
        flags=0,
        object_id="Q1",
        metadata_blocks=[{"operation": "update", "element": "primary", "token": "secret"}],
        component_blocks=[protocol.ComponentBlock(component_id="primary", content=b"data")],
    )

    with pytest.raises(protocol.ProtocolError, match="update authorization is not configured"):
        await handlers.handle_update(request, registry)


@pytest.mark.asyncio
async def test_handle_invoke_returns_workflow_results(monkeypatch):
    """Ensure invoke handler returns workflow metadata and derived components.

    Args:
        monkeypatch: Pytest monkeypatch fixture.

    Returns:
        None
    """
    registry = StubRegistry([])
    workflow_result = {
        "workflow": "equation_extraction",
        "sourceObject": "Q123",
        "derivedComponents": [
            {
                "componentId": "doip:bitstream/Q123/equations-json",
                "mediaType": "application/json",
                "size": 10,
            }
        ],
        "createdItems": ["Q999"],
    }

    async def fake_workflow(qid, params):
        """Return canned workflow results for invoke handler tests.

        Args:
            qid: Requested object identifier.
            params: Workflow parameters.

        Returns:
            dict: Stubbed workflow result.
        """
        return workflow_result

    monkeypatch.setattr(handlers.workflows, "run_equation_extraction_workflow", fake_workflow)
    async def fake_get_component_bytes(object_id, component_id="primary"):
        """Return stubbed workflow-derived component bytes.

        Args:
            object_id: Requested object identifier.
            component_id: Component identifier being fetched.
            media_type: Media type (unused).
            extension: Extension (unused).

        Returns:
            bytes: Dummy workflow content.
        """
        return b"{}"

    monkeypatch.setattr(handlers.storage_lakefs, "get_component_bytes", fake_get_component_bytes)

    request = protocol.DOIPMessage(
        version=protocol.DOIP_VERSION,
        msg_type=protocol.MSG_TYPE_REQUEST,
        operation=protocol.OP_INVOKE,
        flags=0,
        object_id="Q123",
        metadata_blocks=[{"workflow": "equation_extraction", "params": {}}],
    )

    response = await handlers.handle_invoke(request, registry)
    assert response.msg_type == protocol.MSG_TYPE_RESPONSE
    assert response.operation == protocol.OP_INVOKE
    assert response.workflow_blocks[0]["workflow"] == "equation_extraction"
    assert len(response.component_blocks) == 1
    comp = response.component_blocks[0]
    assert comp.component_id == "doip:bitstream/Q123/equations-json"
    assert comp.media_type == "application/json"
    assert comp.content == b"{}"


@pytest.mark.asyncio
async def test_handle_retrieve_uses_registry_and_storage(monkeypatch):
    """Retrieve on base PID returns metadata only; no storage access."""

    class StubRegistry:
        def __init__(self):
            self.fdo_call_count = 0

        async def fetch_fdo_object(self, pid):
            self.fdo_call_count += 1
            return {"@id": f"https://fdo.portal/fdo/{pid}"}

    registry = StubRegistry()

    # verify storage backend is NOT called for metadata PIDs
    async def fake_get_bytes(qid, comp):
        assert False, "Should not fetch bitstream bytes for non-bitstream PID"

    async def fake_ensure():
        return True

    monkeypatch.setattr(handlers.storage_lakefs, "ensure_lakefs_available", fake_ensure)
    monkeypatch.setattr(handlers.storage_lakefs, "get_component_bytes", fake_get_bytes)

    request = protocol.DOIPMessage(
        version=protocol.DOIP_VERSION,
        msg_type=protocol.MSG_TYPE_REQUEST,
        operation=protocol.OP_RETRIEVE,
        flags=0,
        object_id="Q123",
        metadata_blocks=[],
    )

    response = await handlers.handle_retrieve(request, registry)

    assert response.msg_type == protocol.MSG_TYPE_RESPONSE
    assert response.operation == protocol.OP_RETRIEVE
    assert len(response.metadata_blocks) == 1
    assert response.component_blocks == []
    assert registry.fdo_call_count == 1



def _load_config_or_skip() -> dict:
    """Load config.yaml from repo root or skip if unavailable/invalid."""
    cfg_path = Path(__file__).resolve().parents[2] / "config.yaml"
    if not cfg_path.exists():
        pytest.skip("config.yaml not present; skipping lakeFS integration test")
    with cfg_path.open("r", encoding="utf-8") as fh:
        cfg = yaml.safe_load(fh) or {}
    if not isinstance(cfg, dict):
        pytest.skip("config.yaml does not contain a mapping")
    return cfg
