from __future__ import annotations

import asyncio
from typing import Dict, List
from urllib.parse import parse_qs, unquote, urlparse

from . import storage_lakefs
from .logging_config import log


class ObjectRegistry:
    """Caches manifests and component metadata for DOIP objects."""

    def __init__(self):
        """Initialize registry caches and shared state."""
        self._manifest_cache: Dict[str, tuple[Dict, str]] = {}  # pid -> (manifest, repo)
        self._lock = asyncio.Lock()

    async def fetch_fdo_object(self, pid: str) -> Dict:
        """Fetch and cache the FDO JSON-LD for a given PID.

        Args:
            pid: PID/QID to retrieve.

        Returns:
            Dict: Manifest JSON-LD payload for the PID.
        """
        manifest, _ = await self._resolve(pid)
        return manifest

    async def get_repo(self, pid: str) -> str:
        """Return the lakeFS repo where the object's metadata was found.

        Args:
            pid: PID/QID to look up.

        Returns:
            str: Repo name.
        """
        _, repo = await self._resolve(pid)
        return repo

    async def purge(self, pid: str) -> None:
        """Remove a PID from the manifest cache, forcing a fresh fetch on next access.

        Args:
            pid: PID/QID to evict from the cache.
        """
        pid = pid.upper()
        async with self._lock:
            self._manifest_cache.pop(pid, None)
        log.info(f"Cache purged for {pid}.")

    async def get_component(self, object_id: str, component_id: str) -> tuple[bytes, str]:
        """Resolve a component via manifest and load its bytes from storage.

        Args:
            object_id: PID/QID containing the component.
            component_id: Identifier of the component to load.

        Returns:
            tuple[bytes, str]: Component content and resolved media type.

        Raises:
            RuntimeError: When the storage backend is unavailable or errors.
            KeyError: When the component is missing.
        """
        log.info(f"get_component() for {object_id}/{component_id}")

        manifest, repo = await self._resolve(object_id)
        component = _find_component(component_id, manifest)
        if component is None:
            raise KeyError(f"component-not-found:{component_id}")

        media_type = _component_media_type(component)

        if not await storage_lakefs.ensure_lakefs_available():
            raise ConnectionError()

        try:
            content = await storage_lakefs.get_component_bytes(object_id, component_id, repo)
        except KeyError as exc:
            raise KeyError(f"component-not-found:{component_id}") from exc
        except Exception as exc:  # noqa: BLE001
            raise RuntimeError("storage-backend error") from exc

        return content, media_type

    async def get_manifest(self, qid: str) -> Dict:
        """Return the manifest (FDO JSON) for a base QID.

        Args:
            qid: PID/QID to load.

        Returns:
            Dict: Manifest JSON-LD payload.
        """
        return await self.fetch_fdo_object(qid)

    async def _resolve(self, pid: str) -> tuple[Dict, str]:
        """Return (manifest, repo) for a PID, using the cache when available."""
        pid = pid.upper()
        async with self._lock:
            if pid in self._manifest_cache:
                log.info(f"Cache hit for {pid}.")
                return self._manifest_cache[pid]

        log.info("(registry._resolve) Fetching ro-crate-metadata.json for %s", pid)
        manifest, repo = await storage_lakefs.get_rocrate_metadata(pid)

        async with self._lock:
            self._manifest_cache[pid] = (manifest, repo)

        return manifest, repo


def _find_component(component_id: str, manifest: Dict) -> Dict | None:
    """Return the RO-Crate file entity matching ``component_id``.

    Matches against ``components/{component_id}`` as a relative ``@id``, or
    extracts the lakeFS path from a full URL ``@id`` and checks the suffix.

    Args:
        component_id: Target component identifier (e.g. ``input/config.json``).
        manifest: Parsed RO-Crate dict containing an ``@graph`` list.

    Returns:
        dict | None: Matching file entity or ``None`` if not found.
    """
    graph = manifest.get("@graph", []) if isinstance(manifest, dict) else []
    target_id = f"components/{component_id}"
    for entity in graph:
        entity_type = entity.get("@type")
        types = [entity_type] if isinstance(entity_type, str) else (entity_type or [])
        if "File" not in types:
            continue
        entity_id = entity.get("@id", "")
        if entity_id == target_id:
            return entity
        if entity_id.startswith(("http://", "https://")):
            path_param = parse_qs(urlparse(entity_id).query).get("path", [""])[0]
            if unquote(path_param).endswith(target_id):
                return entity
    return None


def _component_media_type(component: Dict) -> str:
    """Return the media type for a RO-Crate file entity."""
    return (
        component.get("encodingFormat")
        or component.get("mediaType")
        or component.get("mimeType")
        or "application/octet-stream"
    )
