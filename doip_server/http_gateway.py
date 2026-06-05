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

from fastapi import FastAPI, HTTPException, Path as FastPath, Query, Request, Response
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import HTMLResponse, StreamingResponse
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

_cors_origins = [o.strip() for o in os.getenv("CORS_ALLOWED_ORIGINS", "").split(",") if o.strip()]
if _cors_origins:
    app.add_middleware(
        CORSMiddleware,
        allow_origins=_cors_origins,
        allow_methods=["GET"],
        allow_headers=["Range"],
        expose_headers=["Content-Range", "Accept-Ranges", "Content-Length"],
    )

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
async def download_component(request: Request, object_id: str, component_id: str, force_reload: str | None = Query(None)):
    """Stream a DOIP component to the caller as an HTTP download.

    Supports the ``Range`` request header (RFC 7233) so that clients such as
    DuckDB-wasm can fetch Parquet footers and row-groups without downloading
    the entire file.

    Args:
        request: Incoming HTTP request (used to read the ``Range`` header).
        object_id: PID/QID of the target object.
        component_id: Component identifier to retrieve.

    Returns:
        Response: Full (200) or partial (206) component bytes with appropriate
            HTTP headers including ``Accept-Ranges`` and ``Content-Length``.

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
    content = comp.content
    total = len(content)

    log.info(
        "Serving component", extra={"object_id": object_id, "component_id": component_id, "media_type": media_type, "size": total}
    )

    base_headers = {
        "Content-Disposition": f'inline; filename="{filename}"',
        "Accept-Ranges": "bytes",
        "Content-Length": str(total),
    }

    range_header = request.headers.get("range")
    if range_header:
        try:
            spec = range_header.removeprefix("bytes=")
            start_str, end_str = spec.split("-", 1)
            start = int(start_str)
            end = int(end_str) if end_str else total - 1
            end = min(end, total - 1)
            if start > end or start >= total:
                raise HTTPException(
                    status_code=416,
                    detail="Range Not Satisfiable",
                    headers={"Content-Range": f"bytes */{total}"},
                )
            chunk = content[start : end + 1]
            log.info("Serving range bytes=%d-%d/%d", start, end, total, extra={"object_id": object_id})
            return Response(
                content=chunk,
                status_code=206,
                media_type=media_type,
                headers={**base_headers, "Content-Range": f"bytes {start}-{end}/{total}", "Content-Length": str(len(chunk))},
            )
        except HTTPException:
            raise
        except Exception:
            log.warning("Malformed Range header '%s', serving full content", range_header)

    return Response(content=content, media_type=media_type, headers=base_headers)


@app.get("/{object_id}", response_class=HTMLResponse)
async def pid_hint(object_id: str = FastPath(..., pattern=r"^[Qq]\d+$")):
    """Return a hint page for bare QID paths like /Q123.

    FastAPI's path validation ensures this only fires for QID-shaped segments,
    so static assets (/favicon.ico, /background.png, …) still reach the mount.
    """
    qid = object_id.upper()
    retrieve_url = f"/doip/retrieve/{qid}"
    html = f"""<!DOCTYPE html>
<html>
  <head>
    <meta charset="utf-8">
    <title>Not found – EPISERVE DOIP</title>
    <link rel="icon" href="/favicon.ico">
    <style>
      body {{
        margin: 0; padding: 0;
        font-family: Arial, sans-serif;
        color: #0b132b;
        background: url('/background_mardi_api.png') no-repeat center center fixed;
        background-size: cover;
      }}
      .overlay {{
        background-color: rgba(255, 255, 255, 0.85);
        max-width: 720px;
        margin: 12vh auto;
        padding: 32px 36px;
        border-radius: 12px;
        box-shadow: 0 10px 30px rgba(0,0,0,0.15);
      }}
      a {{ color: #1a73e8; text-decoration: none; font-weight: 600; }}
      a:hover {{ text-decoration: underline; }}
      code {{ background: #f1f3f4; padding: 2px 6px; border-radius: 4px; }}
    </style>
  </head>
  <body>
    <div class="overlay">
      <p>Nothing found at <code>/{object_id}</code>.</p>
      <p>Did you mean: <a href="{retrieve_url}">{retrieve_url}</a>?</p>
    </div>
  </body>
</html>"""
    return HTMLResponse(content=html, status_code=404)


_LANDING_CANDIDATES = [
    Path("/app/landing"),
    Path(__file__).parent.parent / "docker" / "landing",
]
_landing_dir = next((p for p in _LANDING_CANDIDATES if p.is_dir()), None)
if _landing_dir:
    app.mount("/", StaticFiles(directory=str(_landing_dir), html=True), name="landing")
