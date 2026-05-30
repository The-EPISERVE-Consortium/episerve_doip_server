"""HTTP download gateway that bridges browser requests to the DOIP server.

The gateway exposes a simple REST-style endpoint that accepts an object ID and
component ID via the path, fetches the corresponding component from the
co-located DOIP server, and streams the content back with appropriate HTTP
headers so browsers treat it as a file download.
"""

from __future__ import annotations

import os
import asyncio
import ssl
from pathlib import Path
from urllib.parse import urlparse

from fastapi import FastAPI, HTTPException, Query
from fastapi.responses import StreamingResponse
from fastapi.staticfiles import StaticFiles

from doip_client import StrictDOIPClient
from doip_server.logging_config import log


def _parse_host(raw: str | None) -> str:
    """Return a host string, handling values like ``tcp://host:port``.

    Args:
        raw: Raw host value from the environment.

    Returns:
        str: Hostname portion suitable for socket connections.
    """

    if not raw:
        return "127.0.0.1"
    parsed = urlparse(raw)
    return parsed.hostname or raw


def _parse_port(raw: str | None, default: int = 3567) -> int:
    """Return an integer port, tolerating Kubernetes-style ``tcp://HOST:PORT`` envs.

    Args:
        raw: Raw port value from the environment.
        default: Fallback port when parsing fails.

    Returns:
        int: Parsed port number.
    """

    if raw is None:
        return default
    try:
        return int(raw)
    except ValueError:
        parsed = urlparse(raw)
        if parsed.port:
            return parsed.port
        # Fallback for values like host:port without scheme
        try:
            maybe_port = raw.rsplit(":", 1)[-1]
            return int(maybe_port)
        except Exception:
            log.warning("Invalid DOIP_PORT value '%s', falling back to %s", raw, default)
            return default


def _resolve_backend() -> tuple[str, int]:
    """Return host/port for the DOIP binary server, with a safe fallback.

    Environment precedence:
    1) DOIP_BACKEND_HOST / DOIP_BACKEND_PORT
    2) DOIP_HOST / DOIP_PORT
    3) Defaults: 127.0.0.1:3567
    """
    raw_host = os.getenv("DOIP_BACKEND_HOST") or os.getenv("DOIP_HOST")
    raw_port = os.getenv("DOIP_BACKEND_PORT") or os.getenv("DOIP_PORT")
    host = _parse_host(raw_host)
    port = _parse_port(raw_port, default=3567)
    if port == 80:
        log.warning(
            "DOIP backend port resolved to 80 (likely the HTTP gateway); falling back to 3567",
            extra={"host": host, "port": port},
        )
        port = 3567
    return host, port


DEFAULT_DOIP_HOST, DEFAULT_DOIP_PORT = _resolve_backend()
CERT_PATH = Path("certs/server.crt")


def _should_use_tls(raw: str | None) -> tuple[bool, str]:
    """Return whether TLS should be used, with a reason string.

    Args:
        raw: Optional env-provided value for ``DOIP_USE_TLS``.

    Returns:
        tuple[bool, str]: (use_tls flag, reason description).
    """
    if isinstance(raw, str):
        lowered = raw.strip().lower()
        if lowered in ("1", "true", "yes", "on"):
            return True, "DOIP_USE_TLS env forced on"
        if lowered in ("0", "false", "no", "off"):
            return False, "DOIP_USE_TLS env forced off"
    if CERT_PATH.exists():
        return True, f"certificate present at {CERT_PATH}"
    return False, f"certificate missing at {CERT_PATH}"


def _client(use_tls: bool | None = None) -> StrictDOIPClient:
    """Create a StrictDOIPClient configured for the local server.

    Args:
        use_tls: Optional override for TLS usage. If ``None``, TLS is enabled
            when the container has a server certificate present.

    Returns:
        StrictDOIPClient: Configured client instance.
    """

    tls_enabled, reason = _should_use_tls(os.getenv("DOIP_USE_TLS")) if use_tls is None else (use_tls, "explicit override")
    verify_tls = os.getenv("DOIP_VERIFY_TLS", "false").lower() == "true"
    log.info(
        "Constructed DOIP client",
        extra={
            "host": DEFAULT_DOIP_HOST,
            "port": DEFAULT_DOIP_PORT,
            "use_tls": tls_enabled,
            "verify_tls": verify_tls,
            "reason": reason,
        },
    )

    return StrictDOIPClient(
        host=DEFAULT_DOIP_HOST,
        port=DEFAULT_DOIP_PORT,
        use_tls=tls_enabled,
        verify_tls=verify_tls,
    )


app = FastAPI(title="MaRDI DOIP HTTP Gateway")

@app.on_event("startup")
async def on_startup():
    log.info(
        "HTTP Gateway started",
        extra={"host": DEFAULT_DOIP_HOST, "port": DEFAULT_DOIP_PORT}
    )

@app.post("/doip/purge/{object_id}")
async def purge_object(object_id: str):
    """Purge the server-side manifest cache for an object.

    Args:
        object_id: PID/QID whose cached manifest should be evicted.

    Returns:
        dict: Confirmation payload from the DOIP server.
    """
    log.info("Cache purge requested", extra={"object_id": object_id})
    client = _client()
    try:
        result = await asyncio.to_thread(client.purge, object_id)
    except Exception as exc:
        log.exception("Purge failed", extra={"object_id": object_id})
        raise HTTPException(status_code=502, detail=f"Purge error: {exc}")
    return result


@app.get("/doip/retrieve/{object_id}")
async def retrieve_metadata(object_id: str):
    """Return the RO-Crate metadata for an object as JSON.

    Args:
        object_id: PID/QID of the target object.

    Returns:
        dict: RO-Crate metadata from the DOIP server.
    """
    log.info("HTTP metadata requested", extra={"object_id": object_id})
    client = _client()
    try:
        response = await asyncio.to_thread(client.retrieve, object_id)
    except Exception as exc:
        log.exception("DOIP backend error during metadata retrieve", extra={"object_id": object_id})
        raise HTTPException(status_code=502, detail=f"DOIP backend error: {exc}")
    if not response.metadata_blocks:
        raise HTTPException(status_code=404, detail="Object not found")
    return response.metadata_blocks[0]


@app.get("/doip/retrieve/{object_id}/{component_id:path}")
async def download_component(object_id: str, component_id: str, force_reload: str | None = Query(None)):
    """Stream a DOIP component to the caller as an HTTP download.

    Args:
        object_id: PID/QID of the target object.
        component_id: Component identifier to retrieve.

    Returns:
        StreamingResponse: Component bytes with appropriate HTTP headers.

    Raises:
        HTTPException: When the component is missing or backend errors occur.
    """

    log.info("HTTP download requested", extra={"object_id": object_id, "component_id": component_id, "force_reload": force_reload is not None})

    client = _client()
    if force_reload is not None:
        try:
            await asyncio.to_thread(client.purge, object_id)
        except Exception as exc:
            log.warning("Purge before reload failed, proceeding anyway", extra={"object_id": object_id}, exc_info=exc)
    try:
        response = await asyncio.to_thread(client.retrieve, object_id, component_id)
    except ssl.SSLError as exc:
        log.warning(
            "TLS handshake with DOIP backend failed; retrying without TLS",
            extra={"object_id": object_id, "component_id": component_id},
            exc_info=exc,
        )
        client = _client(use_tls=False)
        response = await asyncio.to_thread(client.retrieve, object_id, component_id)
    except ConnectionError as exc:
        log.error(
            "Connection to DOIP backend closed unexpectedly; verify DOIP_BACKEND_HOST/PORT and TLS settings",
            extra={"object_id": object_id, "component_id": component_id},
            exc_info=exc,
        )
        raise HTTPException(status_code=502, detail="DOIP backend connection closed unexpectedly")
    except Exception as exc:  # noqa: BLE001
        log.exception(
            "DOIP backend error during retrieve", extra={"object_id": object_id, "component_id": component_id}
        )
        raise HTTPException(status_code=502, detail=f"DOIP backend error: {exc}")

    if not response.component_blocks:
        log.warning(
            "Component not found", extra={"object_id": object_id, "component_id": component_id}
        )
        raise HTTPException(status_code=404, detail="Component not found")

    comp = response.component_blocks[0]
    media_type = comp.media_type or "application/octet-stream"
    filename = Path(comp.component_id).name or "download"

    log.info(
        "Serving component", extra={"object_id": object_id, "component_id": component_id, "media_type": media_type}
    )

    return StreamingResponse(
        iter([comp.content]),
        media_type=media_type,
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )


_LANDING_CANDIDATES = [
    Path("/app/landing"),
    Path(__file__).parent.parent / "docker" / "landing",
]
_landing_dir = next((p for p in _LANDING_CANDIDATES if p.is_dir()), None)
if _landing_dir:
    app.mount("/", StaticFiles(directory=str(_landing_dir), html=True), name="landing")
