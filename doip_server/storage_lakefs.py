from __future__ import annotations

import asyncio
import json
from functools import lru_cache
from typing import AsyncGenerator, Dict, List, Tuple

import boto3
import httpx
from botocore.client import Config, BaseClient

from .logging_config import log
from doip_shared.sharding import get_component_path, shard_qid

_CFG: Dict = {}

def configure(cfg: Dict) -> None:
    """Configure lakeFS storage module with application settings.

    Args:
        cfg: Configuration dictionary produced by doip_server.main.set_config().
    """
    global _CFG
    _CFG = cfg or {}
    try:
        _client.cache_clear()
    except Exception:
        # If the client is not yet defined or cacheable, ignore.
        pass
    try:
        _lakefs_api_client.cache_clear()
    except Exception:
        # If the client is not yet defined or cacheable, ignore.
        pass


def _repos() -> list[str]:
    """Return ordered list of repository names tried during reads.

    Raises:
        ValueError: if no lakeFS repositories are configured.
    """
    lakefs_cfg = _CFG.get("lakefs", {}) if isinstance(_CFG, dict) else {}
    repos = lakefs_cfg.get("repos", [])
    if not repos:
        raise ValueError("lakeFS repositories are not configured.")
    return repos


def _branch() -> str:
    """Return branch name for lakeFS-backed storage.

    Returns:
        str: Branch name configured for the lakeFS repository.
    """
    lakefs_cfg = _CFG.get("lakefs", {}) if isinstance(_CFG, dict) else {}
    return lakefs_cfg.get("branch") or "main"


def get_update_token() -> str | None:
    """Return the shared secret used to authorize update requests.

    For now, the update token is the configured lakeFS password.

    Returns:
        str | None: Configured shared secret, or ``None`` when unavailable.
    """
    lakefs_cfg = _CFG.get("lakefs", {}) if isinstance(_CFG, dict) else {}
    token = lakefs_cfg.get("password")
    return token if isinstance(token, str) and token else None


def _endpoint_url() -> str | None:
    """Resolve the lakeFS/S3-compatible endpoint URL.

    Returns:
        Optional[str]: Endpoint URL or None for default boto behavior.
    """
    lakefs_cfg = _CFG.get("lakefs", {}) if isinstance(_CFG, dict) else {}

    url = lakefs_cfg.get("url")
    if isinstance(url, str):
        trimmed_url = url.strip()
        if trimmed_url and not trimmed_url.startswith(("http://", "https://")):
            lakefs_cfg["url"] = f"https://{trimmed_url}"
            log.info("Normalized lakefs.url to %s", lakefs_cfg["url"])

    return lakefs_cfg.get("url")


async def ensure_lakefs_available() -> bool:
    """Verify lakeFS/S3 endpoint is configured and reachable.

    Returns:
        bool: True if available, False otherwise.
    """
    endpoint = _endpoint_url()

    log.debug("Checking lakeFS server @: %s", endpoint)

    if not endpoint:
        return False
    try:
        async with httpx.AsyncClient(timeout=3.0, verify=False) as client:
            resp = await client.get(endpoint)
            resp.raise_for_status()
        return True
    except Exception:
        return False


@lru_cache(maxsize=1)
def _client() -> BaseClient:
    """Create a cached boto3 client configured for lakeFS.

    Returns:
        botocore.client.S3: Configured client instance.
    """
    lakefs_cfg = _CFG.get("lakefs", {}) if isinstance(_CFG, dict) else {}
    return boto3.client(
        "s3",
        endpoint_url=_endpoint_url(),
        aws_access_key_id=lakefs_cfg.get("user"),
        aws_secret_access_key=lakefs_cfg.get("password"),
        config=Config(
            signature_version=lakefs_cfg.get("signature_version") or "s3v4",
            s3={"addressing_style": "path"},
        ),
    )

def build_object_key(qid: str, component_id: str, branch: str | None = None) -> str:
    """Return the full lakeFS key (including branch) for a component.

    Args:
        qid: QID of the object.
        component_id: Component identifier.
        branch: Optional branch override; defaults to configured branch.

    Returns:
        str: LakeFS object key suitable for S3 operations.
    """
    branch_name = branch or _branch()
    path = build_object_path(qid, component_id)
    return f"{branch_name}/{path}"


def build_object_path(qid: str, component_id: str) -> str:
    """Return the branch-relative lakeFS path for a component."""
    return get_component_path(qid, component_id, "")


def build_component_object_path(object_id: str, component_id: str) -> str:
    """Return the branch-relative lakeFS path for a specific component."""
    qid = _extract_qid(object_id)
    return build_object_path(qid, component_id)


async def get_component_bytes(object_id: str, component_id: str, repo: str, version: str | None = None) -> bytes:
    """Fetch component content bytes from lakeFS/S3 using sharded paths.

    Args:
        object_id: Object identifier/QID.
        component_id: Component identifier (e.g. "fulltext").
        repo: lakeFS repository name to read from.
        version: Commit ID to read from, or None/"latest" for the current branch.

    Returns:
        bytes: Component content.

    Raises:
        KeyError: If the component is not found in storage.
    """
    qid = _extract_qid(object_id)
    ref = version if (version and version != "latest") else None
    key = build_object_key(qid, component_id, branch=ref)

    log.info("Retrieving lakeFS object repo=%s key=%s", repo, key)

    try:
        response = await asyncio.to_thread(_client().get_object, Bucket=repo, Key=key)
    except Exception as exc:
        raise KeyError(f"S3 object not found: {key}") from exc

    return response["Body"].read()

async def put_component_bytes(
    object_id: str,
    component_id: str,
    data: bytes,
    repo: str,
    media_type: str = "application/octet-stream",
) -> str:
    """Store component bytes to lakeFS and return the object key.

    Args:
        object_id: Object identifier/QID.
        component_id: Component identifier to store.
        data: Content bytes to upload.
        repo: lakeFS repository name to write to.
        media_type: MIME type stored as object metadata.

    Returns:
        str: Stored lakeFS key (branch + sharded path).
    """
    qid = _extract_qid(object_id)
    object_path = build_object_path(qid, component_id)
    key = build_object_key(qid, component_id)

    def _upload() -> None:
        _lakefs_branch(repo=repo).object(object_path).upload(
            data,
            mode="wb",
            content_type=media_type,
        )

    await asyncio.to_thread(_upload)
    return key


@lru_cache(maxsize=1)
def _lakefs_api_client():
    """Create a cached official lakeFS SDK client for branch operations."""
    import lakefs

    lakefs_cfg = _CFG.get("lakefs", {}) if isinstance(_CFG, dict) else {}
    return lakefs.Client(
        host=_endpoint_url(),
        username=lakefs_cfg.get("user"),
        password=lakefs_cfg.get("password"),
    )


def _lakefs_branch(branch: str | None = None, repo: str | None = None):
    """Return a lakeFS branch handle for commit/reset operations."""
    import lakefs

    return lakefs.repository(repo, client=_lakefs_api_client()).branch(branch or _branch())


async def commit_changes(
    message: str,
    repo: str,
    metadata: Dict[str, str] | None = None,
    branch: str | None = None,
    allow_empty: bool = True,
) -> Dict[str, str]:
    """Create a lakeFS commit on the target branch.

    Args:
        message: Commit message.
        repo: lakeFS repository name to commit to.
        metadata: Optional key/value metadata for the commit.
        branch: Branch override; defaults to configured branch.
        allow_empty: Whether to allow commits with no changes.
    """

    def _commit() -> Dict[str, str]:
        ref = _lakefs_branch(branch, repo=repo).commit(
            message=message,
            metadata=metadata or {},
            allow_empty=allow_empty,
        )
        return {
            "repo": repo,
            "branch": branch or _branch(),
            "commit_id": ref.id,
        }

    try:
        return await asyncio.to_thread(_commit)
    except Exception as exc:  # noqa: BLE001
        raise RuntimeError(f"lakeFS commit failed on branch {branch or _branch()}") from exc


async def reset_uncommitted_object(object_path: str, repo: str, branch: str | None = None) -> None:
    """Reset one uncommitted object path on the target branch.

    Args:
        object_path: Branch-relative path of the object to reset.
        repo: lakeFS repository name.
        branch: Branch override; defaults to configured branch.
    """

    def _reset() -> None:
        _lakefs_branch(branch, repo=repo).reset_changes(path_type="object", path=object_path)

    await asyncio.to_thread(_reset)


async def get_fdo_metadata(qid: str) -> tuple[dict, str]:
    """Fetch FDO metadata for a QID from each configured repo.

    Args:
        qid: Object identifier/QID.

    Returns:
        tuple[dict, str]: Parsed metadata JSON and the repo it was found in.

    Raises:
        KeyError: If no metadata file is found in any configured repo.
    """
    shard = shard_qid(qid)
    branch = _branch()
    key = f"{branch}/{shard}/{qid}.fdo.json"
    for repo in _repos():
        log.info("Fetching metadata repo=%s key=%s", repo, key)
        try:
            response = await asyncio.to_thread(_client().get_object, Bucket=repo, Key=key)
            return json.loads(response["Body"].read()), repo
        except Exception:
            continue
    raise KeyError(f"No FDO metadata file found for {qid} in any configured repo")


async def list_components(object_id: str, repo: str) -> List[str]:
    """List component keys under a given object prefix.

    Args:
        object_id: Object identifier/QID.
        repo: lakeFS repository name to list from.

    Returns:
        List[str]: Component suffixes stored for the object.
    """
    qid = _extract_qid(object_id)
    prefix = f"{_branch()}/{shard_qid(qid)}/components/"

    log.info(
        "Listing components repo=%s branch=%s prefix=%s object_id=%s",
        repo,
        _branch(),
        prefix,
        object_id,
    )

    paginator = _client().get_paginator("list_objects_v2")
    result: List[str] = []
    async for page in _async_paginate(paginator, Bucket=repo, Prefix=prefix):
        for obj in page.get("Contents", []):
            key = obj["Key"]
            suffix = key[len(prefix):]
            if key.startswith(prefix) and suffix:
                result.append(suffix)
    return result


async def _async_paginate(paginator, **kwargs) -> AsyncGenerator[Dict, None]:
    """Iterate over paginator pages in a thread to avoid blocking the loop.

    Args:
        paginator: Boto paginator.
        **kwargs: Pagination parameters.

    Yields:
        dict: Paginator page dictionary.
    """
    for page in await asyncio.to_thread(lambda: list(paginator.paginate(**kwargs))):
        yield page

async def list_versions_for_object(qid: str, repo: str) -> List[Dict]:
    """Return the commit history for a QID from lakeFS, newest first.

    Args:
        qid: Bare QID (e.g. "Q1748526042817"), already normalised to uppercase.
        repo: lakeFS repository name the object lives in.

    Returns:
        List[Dict]: Version dicts with keys commit_id, timestamp, message, committer.
        Empty list when no commits touch the object prefix.
    """
    prefix = shard_qid(qid) + "/"

    def _collect() -> List[Dict]:
        branch = _lakefs_branch(repo=repo)
        return [
            {
                "commit_id": commit.id,
                "timestamp": commit.creation_date,
                "message": commit.message,
                "committer": commit.committer,
            }
            for commit in branch.log(prefixes=[prefix])
        ]

    try:
        return await asyncio.to_thread(_collect)
    except Exception as exc:
        log.warning("list_versions_for_object failed for qid=%s repo=%s: %s", qid, repo, exc)
        raise


def _extract_qid(object_id: str) -> str:
    """Normalize and validate an object identifier, returning its QID prefix.

    Args:
        object_id: Object identifier that should start with a leading ``Q``.

    Returns:
        str: Uppercased QID prefix (e.g., ``Q123``).

    Raises:
        ValueError: If the identifier is malformed or missing digits.
    """
    obj = object_id.upper()
    if not obj.startswith("Q"):
        raise ValueError("invalid identifier: must start with Q")

    i = 1
    n = len(obj)
    while i < n and obj[i].isdigit():
        i += 1

    qid = obj[:i]
    if len(qid) == 1:
        raise ValueError("invalid identifier: no digits after Q")

    return qid
