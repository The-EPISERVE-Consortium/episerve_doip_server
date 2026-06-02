import asyncio
import logging
import os
import tempfile
from pathlib import Path

import pytest
import yaml

from doip_server import storage_lakefs


def _config_path() -> Path:
    """Return the repository-level config.yaml path."""
    return Path(__file__).resolve().parents[2] / "config.yaml"


def _load_config_or_skip() -> dict:
    """Load config.yaml or skip if missing/invalid."""
    cfg_path = _config_path()
    if not cfg_path.exists():
        pytest.skip("config.yaml not present; skipping lakeFS integration test")
    with cfg_path.open("r", encoding="utf-8") as fh:
        cfg = yaml.safe_load(fh) or {}
    if not isinstance(cfg, dict):
        pytest.skip("config.yaml does not contain a mapping")
    return cfg


@pytest.mark.asyncio
async def test_storage_lakefs_lists_components_from_config():
    """Attempt to list components using config.yaml-driven lakeFS settings."""
    cfg = _load_config_or_skip()
    lakefs_cfg = cfg.get("lakefs") or {}
    if not isinstance(lakefs_cfg, dict) or not lakefs_cfg.get("url"):
        pytest.skip("lakeFS endpoint url not configured in config.yaml")

    storage_lakefs.configure(cfg)

    logging.getLogger(__name__).debug(
        "test_storage_lakefs_lists_components_from_config() \n "
        "Using lakeFS configured with \n url: %s repos: %s branch: %s",
        lakefs_cfg.get("url"),
        lakefs_cfg.get("repos"),
        lakefs_cfg.get("branch"),
    )

    available = await storage_lakefs.ensure_lakefs_available()
    if not available:
        pytest.skip("lakeFS endpoint url unavailable; skipping integration test")

    object_id = "Q6830878"
    repos = lakefs_cfg.get("repos") or []
    repo = repos[0] if repos else pytest.skip("No repos configured")
    components = await storage_lakefs.list_components(object_id, repo)

    assert isinstance(components, list)


@pytest.mark.asyncio
async def test_storage_lakefs_downloads_component_to_tempfile():
    """Download a component to a temp file and verify size matches content length."""
    cfg = _load_config_or_skip()
    lakefs_cfg = cfg.get("lakefs") or {}
    if not isinstance(lakefs_cfg, dict) or not lakefs_cfg.get("url"):
        pytest.skip("lakeFS endpoint url not configured in config.yaml")

    storage_lakefs.configure(cfg)
    if not await storage_lakefs.ensure_lakefs_available():
        pytest.skip("lakeFS url unavailable; skipping download test")

    logging.getLogger(__name__).info(
        "using lakefs: %s",
        lakefs_cfg.get("url"),
    )

    object_id = "Q6830878"
    repos = lakefs_cfg.get("repos") or []
    repo = repos[0] if repos else pytest.skip("No repos configured")
    components = await storage_lakefs.list_components(object_id, repo)
    if not components:
        pytest.skip("No components found for Q6830878 in lakeFS; skipping download test")

    component_key = components[0]
    logging.getLogger(__name__).info(
        "test_storage_lakefs_downloads_component_to_tempfile() downloading component: %s",
        component_key,
    )

    try:
        content = await storage_lakefs.get_component_bytes(object_id, component_key, repo)
    except KeyError:
        pytest.skip(f"Component {component_key!r} not retrievable from lakeFS")

    with tempfile.NamedTemporaryFile(delete=False) as tmp:
        tmp.write(content)
        tmp_path = Path(tmp.name)

    logging.getLogger(__name__).info(
        "Downloaded: %d bytes",
        tmp_path.stat().st_size,
    )

    assert tmp_path.stat().st_size == len(content)
    tmp_path.unlink(missing_ok=True)


@pytest.mark.asyncio
@pytest.mark.lakefs_write
async def test_storage_lakefs_can_put_object_to_sandbox():
    """Manually verify write access to the sandbox repo and commit the uploaded object."""
    """ $env:RUN_LAKEFS_WRITE_TESTS="1"; pytest -s tests/server/test_storage_lakefs.py -q -k sandbox
     """
    print("running test_storage_lakefs_can_put_object_to_sandbox")
    if os.getenv("RUN_LAKEFS_WRITE_TESTS") != "1":
        pytest.skip("manual lakeFS write test disabled; set RUN_LAKEFS_WRITE_TESTS=1 to enable")

    cfg = _load_config_or_skip()
    lakefs_cfg = cfg.get("lakefs") or {}
    if not isinstance(lakefs_cfg, dict) or not lakefs_cfg.get("url"):
        pytest.skip("lakeFS endpoint url not configured in config.yaml")
    if not lakefs_cfg.get("user") or not lakefs_cfg.get("password"):
        pytest.skip("lakeFS credentials not configured in config.yaml")

    sandbox_cfg = {
        **cfg,
        "lakefs": {
            **lakefs_cfg,
            "repos": ["sandbox"],
            "branch": "main",
        },
    }
    print(
        "Using lakeFS sandbox credentials "
        f"url={sandbox_cfg['lakefs'].get('url')} "
        f"repos={sandbox_cfg['lakefs'].get('repos')} "
        f"branch={sandbox_cfg['lakefs'].get('branch')} "
        f"user={sandbox_cfg['lakefs'].get('user')} "
        f"password={sandbox_cfg['lakefs'].get('password')}"
    )
    storage_lakefs.configure(sandbox_cfg)

    if not await storage_lakefs.ensure_lakefs_available():
        pytest.skip("lakeFS endpoint unavailable; skipping write test")

    payload = b"lakefs-write-test"
    key = "test-upload.txt"

    print(f"Attempting sandbox write to lakeFS key: {key}")

    obj = storage_lakefs._lakefs_branch("main", repo="sandbox").object(key)
    await asyncio.to_thread(
        obj.upload,
        payload,
        mode="wb",
    )
    response = await asyncio.to_thread(obj.stat)
    commit = await storage_lakefs.commit_changes(
        message=f"Manual sandbox write test for {key}",
        repo="sandbox",
        metadata={"test": "test_storage_lakefs_can_put_object_to_sandbox", "path": key},
        branch="main",
    )
    print(f"Committed sandbox write with commit_id={commit['commit_id']}")

    assert response.size_bytes == len(payload)
