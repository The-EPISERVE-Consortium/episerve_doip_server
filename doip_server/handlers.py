from __future__ import annotations

import asyncio
import hmac
import mimetypes
import tempfile
from pathlib import Path
from typing import List
from urllib.parse import urlparse

import httpx
from rocrate.rocrate import ROCrate
from rocrate.model.file import File

from . import object_registry, protocol, storage_lakefs, workflows
from .logging_config import log
from .protocol import ComponentBlock, DOIPMessage


async def handle_hello(msg: DOIPMessage, registry: object_registry.ObjectRegistry) -> DOIPMessage:
    """Respond to hello/health check requests with server metadata.

    Args:
        msg: Incoming DOIP hello request.
        registry: Object registry resolver (unused, for signature parity).

    Returns:
        DOIPMessage: Response containing server status and capabilities.
    """
    log.info("Handling hello request for object_id=%s", msg.object_id)
    metadata_block = {
        "operation": "hello",
        "status": "ok",
        "server": "episerve_doip_server",
        "version": protocol.DOIP_VERSION,
        "availableOperations": {
            "hello": protocol.OP_HELLO,
            "retrieve": protocol.OP_RETRIEVE,
            "update": protocol.OP_UPDATE,
            "describe": protocol.OP_DESCRIBE, # not standard
            "invoke": protocol.OP_INVOKE, # not standard
        },
    }

    return DOIPMessage(
        version=protocol.DOIP_VERSION,
        msg_type=protocol.MSG_TYPE_RESPONSE,
        operation=protocol.OP_HELLO,
        flags=0,
        object_id=msg.object_id,
        metadata_blocks=[metadata_block],
    )

async def handle_describe(msg: DOIPMessage, registry: object_registry.ObjectRegistry) -> DOIPMessage:
    """Return the registry description for a PID/QID.

    Args:
        msg: Incoming DOIP describe request.
        registry: Object registry used to resolve manifests.

    Returns:
        DOIPMessage: Response containing the fetched manifest metadata.
    """
    pid = msg.object_id
    fdo_json = await registry.fetch_fdo_object(pid)
    return DOIPMessage(
        version=protocol.DOIP_VERSION,
        msg_type=protocol.MSG_TYPE_RESPONSE,
        operation=protocol.OP_DESCRIBE,
        flags=0,
        object_id=pid,
        metadata_blocks=[fdo_json],
    )


async def handle_retrieve(msg: DOIPMessage, registry: object_registry.ObjectRegistry) -> DOIPMessage:
    """Retrieve metadata or a specific component for a DOIP object.

    If "element" is set in the meta-data block, the server tries to fetch it from the storage.

    If "element" == "rocrate" the server tries to build a rocrate object from the data stored with
    the object.

    Args:
        msg: Incoming DOIP retrieve request.
        registry: Object registry used to fetch manifests/components.

    Returns:
        DOIPMessage: Response containing metadata and optional components.

    Raises:
        KeyError: If a requested component cannot be found.
    """
    pid    = msg.object_id.upper()
    meta   = (msg.metadata_blocks[0] if msg.metadata_blocks else {})
    element   = meta.get("element")  # componentId or None
    version = meta.get("version") or None
    if version == "latest":
        version = None

    log.info("handle_retrieve() for object_id=%s", pid)

    if element == "rocrate":
        try:
            crate, _ = await registry.get_component(pid, "rocrate")
        except KeyError:
            crate = await _build_rocrate_payload(pid, registry)
        except Exception as exc:
            raise KeyError(f"Component id not found: {element}") from exc
        return DOIPMessage(
            version=protocol.DOIP_VERSION,
            msg_type=protocol.MSG_TYPE_RESPONSE,
            operation=protocol.OP_RETRIEVE,
            flags=0,
            object_id=pid,
            metadata_blocks=[],
            component_blocks=[
                ComponentBlock(
                    component_id="rocrate",
                    media_type="application/zip",
                    content=crate,
                    declared_size=len(crate),
                )
            ],
        )

    if element == "versions":
        try:
            repo = await registry.get_repo(pid)
        except KeyError:
            raise KeyError(f"Object not found: {pid}")
        qid = storage_lakefs._extract_qid(pid)
        limit = meta.get("limit")
        if limit is not None:
            limit = int(limit)
        include_sizes = bool(meta.get("include_sizes", False))
        versions = await storage_lakefs.list_versions_for_object(qid, repo, limit=limit, include_sizes=include_sizes)
        return DOIPMessage(
            version=protocol.DOIP_VERSION,
            msg_type=protocol.MSG_TYPE_RESPONSE,
            operation=protocol.OP_RETRIEVE,
            flags=0,
            object_id=pid,
            metadata_blocks=[{"element": "versions", "objectId": pid, "versions": versions}],
            component_blocks=[],
        )

    if element:
        try:
            content, media_type = await registry.get_component(pid, element, version=version)
            size = len(content)
        except Exception as exc:
            raise KeyError(f"Component id not found: {element}") from exc

        return DOIPMessage(
            version=protocol.DOIP_VERSION,
            msg_type=protocol.MSG_TYPE_RESPONSE,
            operation=protocol.OP_RETRIEVE,
            flags=0,
            object_id=pid,
            metadata_blocks=[],
            component_blocks=[
                ComponentBlock(
                    component_id=element,
                    media_type=media_type,
                    content=content,
                    declared_size=size,
                )
            ],
        )

    fdo_json = await registry.fetch_fdo_object(pid)
    return DOIPMessage(
        version=protocol.DOIP_VERSION,
        msg_type=protocol.MSG_TYPE_RESPONSE,
        operation=protocol.OP_RETRIEVE,
        flags=0,
        object_id=pid,
        metadata_blocks=[fdo_json],
        component_blocks=[],
    )


async def handle_update(msg: DOIPMessage, registry: object_registry.ObjectRegistry) -> DOIPMessage:
    """Store one component for an existing object and create a lakeFS commit.

    Args:
        msg: Incoming DOIP update request.
        registry: Object registry used to verify object existence and purge cache.

    Returns:
        DOIPMessage: Response confirming the committed update.

    Raises:
        protocol.ProtocolError: If authorization fails or the request is malformed.
    """
    object_id = msg.object_id.upper()
    metadata = msg.metadata_blocks[0] if msg.metadata_blocks else {}
    element = metadata.get("element")
    _validate_update_token(object_id, metadata)

    await registry.fetch_fdo_object(object_id)
    repo = await registry.get_repo(object_id)

    if len(msg.component_blocks) != 1:
        raise protocol.ProtocolError("update requires exactly one component block")

    component = msg.component_blocks[0]
    if not component.component_id:
        raise protocol.ProtocolError("update component_id is required")
    if element and element != component.component_id:
        raise protocol.ProtocolError("update metadata element must match component block id")

    media_type = component.media_type or "application/octet-stream"
    object_path = storage_lakefs.build_component_object_path(object_id, component.component_id)

    try:
        await storage_lakefs.put_component_bytes(
            object_id,
            component.component_id,
            component.content,
            repo=repo,
            media_type=media_type,
        )
        commit = await storage_lakefs.commit_changes(
            message=f"Update {object_id} component {component.component_id}",
            repo=repo,
            metadata={
                "operation": "update",
                "object_id": object_id,
                "component_id": component.component_id,
                "media_type": media_type,
            },
        )
    except Exception:
        try:
            await storage_lakefs.reset_uncommitted_object(object_path, repo=repo)
        except Exception:
            log.exception("Failed to reset uncommitted lakeFS object for %s", object_path)
        raise

    await registry.purge(object_id)

    return DOIPMessage(
        version=protocol.DOIP_VERSION,
        msg_type=protocol.MSG_TYPE_RESPONSE,
        operation=protocol.OP_UPDATE,
        flags=0,
        object_id=object_id,
        metadata_blocks=[
            {
                "operation": "update",
                "status": "committed",
                "objectId": object_id,
                "componentId": component.component_id,
                "mediaType": media_type,
                "size": len(component.content),
                "branch": commit["branch"],
                "commitId": commit["commit_id"],
            }
        ],
    )


def _validate_update_token(object_id: str, metadata: dict) -> None:
    """Validate the shared secret attached to an update request.

    Args:
        object_id: Target object identifier for logging context.
        metadata: Update metadata block from the request.

    Returns:
        None

    Raises:
        protocol.ProtocolError: If the server is not configured for updates or
            if the provided token is missing or invalid.
    """
    expected_token = storage_lakefs.get_update_token()
    if not expected_token:
        log.error("Rejected update for %s because no update token is configured", object_id)
        raise protocol.ProtocolError("update authorization is not configured")

    provided_token = metadata.get("token")
    if not isinstance(provided_token, str) or not provided_token:
        log.warning("Rejected update for %s because no update token was provided", object_id)
        raise protocol.ProtocolError("update authorization failed")

    if not hmac.compare_digest(provided_token, expected_token):
        log.warning("Rejected update for %s because the update token was invalid", object_id)
        raise protocol.ProtocolError("update authorization failed")


async def handle_invoke(msg: DOIPMessage, registry: object_registry.ObjectRegistry) -> DOIPMessage:
    """Handle DOIP invoke requests by executing supported workflows.

    Args:
        msg: Incoming DOIP invoke request.
        registry: Object registry resolver.

    Returns:
        DOIPMessage: Response with workflow metadata and derived components.
    """
    qid = msg.object_id
    log.info("Handling invoke request for object_id=%s", qid)
    workflow_name, params = _requested_workflow(msg)
    if workflow_name == "equation_extraction":
        result = await workflows.run_equation_extraction_workflow(qid, params)
    else:
        raise protocol.ProtocolError(f"Unsupported workflow {workflow_name}")

    derived_blocks: List[ComponentBlock] = []
    for comp in result.get("derivedComponents", []):
        comp_id = comp["componentId"]
        content = await storage_lakefs.get_component_bytes(
            qid,
            comp_id,
        )
        derived_blocks.append(
            ComponentBlock(
                component_id=comp_id,
                content=content,
                media_type=comp.get("mediaType", "application/octet-stream"),
                declared_size=comp.get("size"),
            )
        )

    metadata_block = {
        "operation": "invoke",
        "workflow": workflow_name,
        "result": result,
    }

    return DOIPMessage(
        version=protocol.DOIP_VERSION,
        msg_type=protocol.MSG_TYPE_RESPONSE,
        operation=protocol.OP_INVOKE,
        flags=0,
        object_id=qid,
        metadata_blocks=[metadata_block],
        component_blocks=derived_blocks,
        workflow_blocks=[result],
    )


async def handle_purge(msg: DOIPMessage, registry: object_registry.ObjectRegistry) -> DOIPMessage:
    """Purge the cached manifest for a PID, forcing a fresh fetch on next access.

    Args:
        msg: Incoming DOIP purge request.
        registry: Object registry whose cache entry will be evicted.

    Returns:
        DOIPMessage: Response confirming the purge.
    """
    pid = msg.object_id
    await registry.purge(pid)
    return DOIPMessage(
        version=protocol.DOIP_VERSION,
        msg_type=protocol.MSG_TYPE_RESPONSE,
        operation=protocol.OP_PURGE,
        flags=0,
        object_id=pid,
        metadata_blocks=[{"status": "purged", "pid": pid.upper()}],
    )


async def handle_list_ops(msg: DOIPMessage, registry: object_registry.ObjectRegistry) -> DOIPMessage:
    """Return the list of supported operations.

    Args:
        msg: Incoming DOIP list-ops request.
        registry: Object registry resolver (unused, included for symmetry).

    Returns:
        DOIPMessage: Response describing available operations.
    """
    log.info("Handling list_ops request for object_id=%s", msg.object_id)
    metadata_block = {
        "operation": "list_operations",
        "availableOperations": {
            "hello": protocol.OP_HELLO,
            "retrieve": protocol.OP_RETRIEVE,
            "update": protocol.OP_UPDATE,
            "invoke": protocol.OP_INVOKE,
        },
    }
    return DOIPMessage(
        version=protocol.DOIP_VERSION,
        msg_type=protocol.MSG_TYPE_RESPONSE,
        operation=protocol.OP_LIST_OPS,
        flags=0,
        object_id=msg.object_id,
        metadata_blocks=[metadata_block],
    )


def _requested_workflow(msg: DOIPMessage):
    """Extract requested workflow name and params from message blocks.

    Args:
        msg: DOIP invoke request message.

    Returns:
        tuple[str, dict]: Workflow name and parameters.

    Raises:
        protocol.ProtocolError: If workflow is missing.
    """
    for meta in msg.metadata_blocks:
        if "workflow" in meta:
            return meta["workflow"], meta.get("params", {})
    for wf in msg.workflow_blocks:
        if "workflow" in wf:
            return wf["workflow"], wf.get("params", {})
    raise protocol.ProtocolError("Workflow not specified in invoke request")


async def _build_rocrate_payload(pid: str, registry) -> bytes:
    """Return the RO-Crate payload for the given PID.

    Strategy:
      1. If a stored ``rocrate`` component exists, return it as-is.
      2. Otherwise, resolve a source URL from the FDO manifest and download it.
      3. If a download succeeds, wrap the file into a minimal RO-Crate ZIP.
      4. If nothing can be resolved, return empty bytes.

    Args:
        pid: PID/QID identifying the dataset object.
        registry: ObjectRegistry instance capable of returning components.

    Returns:
        bytes: RO-Crate bytes if available; empty bytes when absent.

    Raises:
        RuntimeError: If underlying storage access fails (non-missing errors).
    """

    # 1) Try stored rocrate component
    try:
        result = await registry.get_component(pid, "rocrate")
        # tolerate legacy signature returning bytes only
        content = result[0] if isinstance(result, tuple) else result
    except KeyError:
        content = None
    except ConnectionError:
        content = None
    except Exception as exc:
        raise RuntimeError(f"storage error fetching rocrate for {pid}") from exc

    if content is not None:
        return content

    # 2) Resolve a source URL from the manifest
    source_url = await _get_source_url(pid, registry)
    if source_url is None:
        return b""

    # 3) Download and package as RO-Crate
    try:
        log.info("Downloading data from %s", source_url)
        async with httpx.AsyncClient(timeout=30) as client:
            resp = await client.get(source_url)
            resp.raise_for_status()
            payload = resp.content
    except Exception as exc:  # noqa: BLE001
        log.debug("Failed to download rocrate for %s from %s: %s", pid, source_url, exc)
        return b""

    filename = _filename_from_url(source_url, pid)
    media_type = mimetypes.guess_type(filename)[0] or "application/octet-stream"

    tmp_dir = tempfile.TemporaryDirectory()
    tmp_path = Path(tmp_dir.name)
    tmp_file = tmp_path / filename
    tmp_file.write_bytes(payload)

    crate = ROCrate()
    file_entity = File(crate, str(tmp_file), properties={"encodingFormat": media_type, "name": filename})
    crate.add(file_entity)
    crate.root_dataset["name"] = pid
    crate.root_dataset.append_to("hasPart", file_entity)

    out_zip = tmp_path / "crate.zip"
    crate.write_zip(str(out_zip))
    data = out_zip.read_bytes()
    tmp_dir.cleanup()
    return data


async def _get_source_url(pid: str, registry) -> str | None:
    """Return the first distribution/download URL from the FDO manifest.

    For this project the manifest's distribution URLs play the role of P205. The
    lookup is best-effort; errors are logged and surfaced as ``None`` so the main
    workflow is never blocked.

    Args:
        pid: PID/QID for which to resolve distribution URLs.
        registry: ObjectRegistry used to fetch the manifest.

    Returns:
        str | None: First distribution URL if found, otherwise ``None``.
    """
    try:
        manifest = await registry.fetch_fdo_object(pid)
        if not isinstance(manifest, dict):
            return None

        profile = manifest.get("profile")
        distributions = profile.get("distribution") if isinstance(profile, dict) else None
        if not isinstance(distributions, list):
            log.debug("No distribution list in manifest for %s", pid)
            return None

        for dist in distributions:
            if not isinstance(dist, dict):
                continue
            url = dist.get("contentUrl") or dist.get("contentURL") or dist.get("url")
            if isinstance(url, str):
                log.info("Found distribution URL (P205 equivalent) for %s: %s", pid, url)
                return url

        log.debug("No distribution URLs (P205 equivalent) found for %s", pid)
        return None
    except Exception as exc:  # noqa: BLE001
        log.debug("Skipping P205 lookup for %s: %s", pid, exc)
        return None


def _filename_from_url(url: str, pid: str) -> str:
    """Return a filename derived from a URL, with a safe fallback.

    Args:
        url: Source URL.
        pid: PID/QID used as fallback stem.

    Returns:
        str: Filename to use inside the RO-Crate.
    """
    parsed = urlparse(url)
    name = Path(parsed.path).name
    if name:
        return name
    return f"{pid}.bin"
