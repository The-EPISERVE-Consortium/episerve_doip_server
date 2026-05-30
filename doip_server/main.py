from __future__ import annotations

import asyncio
import json
import os
import ssl
import struct
import sys
from argparse import ArgumentParser
from pathlib import Path
from functools import partial

import yaml

from doip_server.storage_lakefs import ensure_lakefs_available
from . import handlers, object_registry, protocol, storage_lakefs
from .logging_config import configure_logging, log

configure_logging()

def set_config() -> dict:
    """Build configuration from local config.yaml overlaid with environment variables.

    Returns:
        dict: Configuration map derived from local file and environment variables.
    """
    path = Path("config.yaml")
    cfg: dict = {}

    # First, load config from local config.yaml if it exists
    try:
        if path.exists():
            with path.open("r", encoding="utf-8") as fh:
                data = yaml.safe_load(fh) or {}
            if not isinstance(data, dict):
                log.warning("Config file %s does not contain a mapping", path)
            else:
                cfg.update(data)
    except Exception as exc:  # noqa: BLE001
        log.warning("Failed to load config from %s: %s", path, exc)

    # If environment variables are set, they override config.yaml values

    ollama_api_key = os.getenv("OLLAMA_API_KEY")
    if ollama_api_key:
        cfg.setdefault("ollama", {})["api_key"] = ollama_api_key

    lakefs_user = os.getenv("LAKEFS_USER")
    if lakefs_user:
        cfg.setdefault("lakefs", {})["user"] = lakefs_user

    lakefs_password = os.getenv("LAKEFS_PASSWORD")
    if lakefs_password:
        cfg.setdefault("lakefs", {})["password"] = lakefs_password

    lakefs_url = os.getenv("LAKEFS_URL")
    if lakefs_url:
        cfg.setdefault("lakefs", {})["url"] = lakefs_url

    lakefs_repo = os.getenv("LAKEFS_REPO")
    if lakefs_repo:
        cfg.setdefault("lakefs", {})["repo"] = lakefs_repo

    # Check whether lakeFS url has the http/s protocol prefix
    lakefs_cfg = cfg.get("lakefs")
    if isinstance(lakefs_cfg, dict):
        url = lakefs_cfg.get("url")
        if isinstance(url, str):
            trimmed_url = url.strip()
            if trimmed_url and not trimmed_url.startswith(("http://", "https://")):
                lakefs_cfg["url"] = f"https://{trimmed_url}"
                log.info("Normalized lakefs.url to %s", lakefs_cfg["url"])

    # Print config
    masked_cfg = _mask_sensitive(cfg)
    log.info("Configuration loaded: %s", masked_cfg)
    return cfg


def _mask_sensitive(data):
    """Return a copy of config data with sensitive values masked.

    Args:
        data: Arbitrary config structure containing nested dicts/lists.

    Returns:
        Same structure with sensitive values partially obfuscated.
    """
    if isinstance(data, dict):
        return {k: _mask_sensitive_value(k, v) for k, v in data.items()}
    if isinstance(data, list):
        return [_mask_sensitive(item) for item in data]
    return data


def _mask_sensitive_value(key: str, value):
    """Mask password values; leave others unchanged.

    Args:
        key: Config key name.
        value: Config value (can be nested structures).

    Returns:
        Value with sensitive strings masked; non-sensitive values untouched.
    """
    if isinstance(value, dict):
        return _mask_sensitive(value)
    if isinstance(value, list):
        return [_mask_sensitive_value(key, item) for item in value]
    if isinstance(value, str) and _is_sensitive_key(key):
        if len(value) <= 6:
            return f"{value[:1]}***{value[-1:]}"
        return f"{value[:3]}***{value[-3:]}"
    return value


def _is_sensitive_key(key: str) -> bool:
    """Return True if the key name indicates sensitive content.

    Args:
        key: Config key name to inspect.

    Returns:
        bool: True if the key likely contains secrets.
    """
    key_lower = key.lower()
    return any(token in key_lower for token in ("password", "secret", "token", "key"))

async def handle_connection(registry: object_registry.ObjectRegistry, reader: asyncio.StreamReader, writer: asyncio.StreamWriter):
    """Process DOIP messages on a single TCP connection.

    Args:
        registry: Object registry instance.
        reader: StreamReader for the client.
        writer: StreamWriter for the client.

    Returns:
        None
    """
    peer = writer.get_extra_info("peername")
    log.info("Connection from %s", peer)
    try:
        while True:
            try:
                msg = await protocol.read_doip_message(reader)
            except asyncio.IncompleteReadError:
                break
            except protocol.ProtocolError as exc:
                log.warning("Protocol error from %s: %s", peer, exc)
                await _send_error(writer, "", exc)
                break

            try:
                response = await dispatch(msg, registry)
            except protocol.ProtocolError as exc:
                log.error("Operation error for %s: %s", peer, exc)
                response = _error_message(msg, exc)
            except Exception as exc:  # noqa: BLE001
                log.exception("Unhandled error for %s", peer)
                response = _error_message(msg, exc)

            writer.write(response.to_bytes())
            await writer.drain()
    finally:
        writer.close()
        await writer.wait_closed()
        log.info("Connection closed %s", peer)


def _metadata_operation_name(msg: protocol.DOIPMessage) -> str | None:
    """Return the requested operation name from metadata if present.

    Args:
        msg: Incoming DOIP message.

    Returns:
        str | None: Operation name or None if missing.
    """
    for meta in msg.metadata_blocks:
        op_name = meta.get("operation")
        if isinstance(op_name, str):
            return op_name
    return None


async def dispatch(msg: protocol.DOIPMessage, registry: object_registry.ObjectRegistry) -> protocol.DOIPMessage:
    """Route a DOIP request to the appropriate handler.

    Args:
        msg: Parsed DOIP request.
        registry: Object registry instance.

    Returns:
        DOIPMessage: Handler response message.
    """
    if msg.msg_type != protocol.MSG_TYPE_REQUEST:
        raise protocol.ProtocolError("Only request messages are supported")

    try:
        op_name = _metadata_operation_name(msg)

        log.info("Dispatching request for %s", op_name)

        if msg.operation == protocol.OP_HELLO or op_name == "hello":
            return await handlers.handle_hello(msg, registry)
        if msg.operation == protocol.OP_RETRIEVE or op_name == "retrieve":
            return await handlers.handle_retrieve(msg, registry)
        if msg.operation == protocol.OP_UPDATE or op_name == "update":
            return await handlers.handle_update(msg, registry)
        if msg.operation == protocol.OP_INVOKE or op_name == "invoke":
            return await handlers.handle_invoke(msg, registry)
        if msg.operation == protocol.OP_LIST_OPS or op_name in ("list_ops", "list_operations"):
            return await handlers.handle_list_ops(msg, registry)
        if msg.operation == protocol.OP_PURGE or op_name == "purge":
            return await handlers.handle_purge(msg, registry)
    except protocol.ProtocolError:
        raise
    except Exception as exc:
        raise protocol.ProtocolError(f"A problem occured in dispatch() for action {op_name}: {exc}")

    raise protocol.ProtocolError(f"Unsupported operation code {msg.operation}")


def _error_message(msg: protocol.DOIPMessage, exc: Exception) -> protocol.DOIPMessage:
    """Build an error response message.

    Args:
        msg: Original DOIP message.
        exc: Exception raised.

    Returns:
        DOIPMessage: Error envelope.
    """
    return protocol.DOIPMessage(
        version=protocol.DOIP_VERSION,
        msg_type=protocol.MSG_TYPE_ERROR,
        operation=msg.operation,
        flags=0,
        object_id=msg.object_id,
        metadata_blocks=[{"error": type(exc).__name__, "message": str(exc)}],
    )


async def _send_error(writer: asyncio.StreamWriter, object_id: str, exc: Exception):
    """Send an error envelope directly to a writer.

    Args:
        writer: Destination writer.
        object_id: Object identifier context.
        exc: Exception to serialize.

    Returns:
        None
    """
    msg = protocol.DOIPMessage(
        version=protocol.DOIP_VERSION,
        msg_type=protocol.MSG_TYPE_ERROR,
        operation=0,
        flags=0,
        object_id=object_id,
        metadata_blocks=[{"error": type(exc).__name__, "message": str(exc)}],
    )
    writer.write(msg.to_bytes())
    await writer.drain()


def _maybe_create_ssl_context() -> ssl.SSLContext | None:
    """Create an SSL context using local certificate/key files if present.

    Returns:
        ssl.SSLContext | None: Configured context or None if certificates are missing.
    """
    cert_path = Path("certs/server.crt")
    key_path = Path("certs/server.key")
    if not cert_path.exists() or not key_path.exists():
        return None
    ctx = ssl.create_default_context(ssl.Purpose.CLIENT_AUTH)
    ctx.load_cert_chain(certfile=str(cert_path), keyfile=str(key_path))
    return ctx


async def handle_compat_connection(
    registry: object_registry.ObjectRegistry, reader: asyncio.StreamReader, writer: asyncio.StreamWriter
):
    """Handle doipy JSON-segmented requests and bridge to DOIP handlers.

    Args:
        registry: Object registry instance.
        reader: Compat StreamReader.
        writer: Compat StreamWriter.

    Returns:
        None
    """
    peer = writer.get_extra_info("peername")
    log.info("Compat connection from %s", peer)
    try:
        try:
            segments = await _read_segments(reader)
        except Exception as exc:  # noqa: BLE001
            log.warning("Compat read failed from %s: %s", peer, exc)
            writer.close()
            await writer.wait_closed()
            return
        if not segments:
            writer.close()
            await writer.wait_closed()
            return
        try:
            request_json = json.loads(segments[0].decode("utf-8"))
        except Exception as exc:  # noqa: BLE001
            log.warning("Compat invalid JSON from %s: %s", peer, exc)
            writer.close()
            await writer.wait_closed()
            return
        response_segments = await _process_compat_request(request_json, registry)
        await _write_segments(writer, response_segments)
    finally:
        writer.close()
        await writer.wait_closed()
        log.info("Compat connection closed %s", peer)


async def _process_compat_request(body: dict, registry: object_registry.ObjectRegistry) -> list[bytes]:
    """Translate a doipy JSON request into a DOIP handler response.

    Args:
        body: Parsed JSON body with compat fields.
        registry: Object registry instance.

    Returns:
        list[bytes]: Segmented response payloads.
    """
    target = body.get("targetId") or body.get("target_id")
    operation = body.get("operationId") or body.get("operation_id")
    attributes = body.get("attributes") or {}
    component = attributes.get("element") or attributes.get("componentId")

    if operation in (protocol.OP_HELLO, "HELLO", "hello", 1):
        msg = protocol.DOIPMessage(
            version=protocol.DOIP_VERSION,
            msg_type=protocol.MSG_TYPE_REQUEST,
            operation=protocol.OP_HELLO,
            flags=0,
            object_id=target or "",
            metadata_blocks=[{"operation": "hello"}],
        )
        response = await handlers.handle_hello(msg, registry)
        return _compat_response_from_doip(response)

    if operation in (protocol.OP_RETRIEVE, "RETRIEVE", "retrieve", 2):
        msg = protocol.DOIPMessage(
            version=protocol.DOIP_VERSION,
            msg_type=protocol.MSG_TYPE_REQUEST,
            operation=protocol.OP_RETRIEVE,
            flags=0,
            object_id=target or "",
            metadata_blocks=[{"components": [component]}] if component else [],
        )
        response = await handlers.handle_retrieve(msg, registry)
        return _compat_response_from_doip(response)

    if operation in (protocol.OP_INVOKE, "INVOKE", "invoke", 5):
        workflow = attributes.get("workflow") or body.get("workflow") or "equation_extraction"
        params = attributes.get("params") or body.get("params") or {}
        msg = protocol.DOIPMessage(
            version=protocol.DOIP_VERSION,
            msg_type=protocol.MSG_TYPE_REQUEST,
            operation=protocol.OP_INVOKE,
            flags=0,
            object_id=target or "",
            metadata_blocks=[{"workflow": workflow, "params": params}],
        )
        response = await handlers.handle_invoke(msg, registry)
        return _compat_response_from_doip(response)

    meta = {"status": "error", "message": f"Unsupported operation {operation}"}
    return [_json_segment(meta)]


def _compat_response_from_doip(msg: protocol.DOIPMessage) -> list[bytes]:
    """Convert a DOIPMessage response into doipy-style segments.

    Args:
        msg: DOIPMessage response to convert.

    Returns:
        list[bytes]: Serialized segments including status and components.
    """
    segments: list[bytes] = []
    status = {
        "status": "success",
        "metadata": msg.metadata_blocks,
    }
    if msg.component_blocks:
        first = msg.component_blocks[0]
        status.setdefault("attributes", {})["filename"] = first.component_id
    segments.append(_json_segment(status))
    for comp in msg.component_blocks:
        segments.append(comp.content)
    return segments


async def _read_segments(reader: asyncio.StreamReader) -> list[bytes]:
    """Read length-prefixed segments terminated by a zero-length segment.

    Args:
        reader: StreamReader providing compat segments.

    Returns:
        list[bytes]: Ordered segments from the stream.
    """
    segments: list[bytes] = []
    while True:
        length_bytes = await reader.readexactly(4)
        length = struct.unpack(">I", length_bytes)[0]
        if length == 0:
            break
        data = await reader.readexactly(length)
        segments.append(data)
    return segments


async def _write_segments(writer: asyncio.StreamWriter, segments: list[bytes]) -> None:
    """Write length-prefixed segments ending with an empty terminator.

    Args:
        writer: Destination StreamWriter.
        segments: Data segments to send.

    Returns:
        None
    """
    for seg in segments:
        writer.write(struct.pack(">I", len(seg)))
        writer.write(seg)
    writer.write(struct.pack(">I", 0))
    await writer.drain()


def _json_segment(data: dict) -> bytes:
    """Serialize a JSON dict to bytes for compat responses.

    Args:
        data: JSON-serializable dictionary.

    Returns:
        bytes: UTF-8 encoded JSON payload.
    """
    return json.dumps(data).encode("utf-8")

async def main(argv: list[str] | None = None):
    """Entrypoint: start the asyncio DOIP TCP server.

    Args:
        argv: Optional list of CLI arguments (defaults to sys.argv).

    Returns:
        None
    """

    # Setup command line parser
    parser = ArgumentParser(description="MaRDI DOIP server")
    parser.add_argument("--port", default="3567", help="Port of this server")
    args = parser.parse_args(argv)

    # Set port
    port = int(args.port)

    # Set config params
    cfg = set_config()
    storage_lakefs.configure(cfg)

    # Initialize registry
    registry = object_registry.ObjectRegistry()

    # Check for lakeFS availablity
    if not await ensure_lakefs_available():
        log.warning("lakeFS server not available!")
    else:
        log.info("DOIP server uses lakeFS server as storage-backend.")

    # Check if SSL certificates are available
    ssl_ctx = _maybe_create_ssl_context()

    # Start binary server
    server = await asyncio.start_server(
        partial(handle_connection, registry), host="0.0.0.0", port=port, ssl=ssl_ctx
    )

    # Start plain server
    compat_server = await asyncio.start_server(
        partial(handle_compat_connection, registry), host="0.0.0.0", port=port + 1, ssl=ssl_ctx
    )

    sockets = ", ".join(str(sock.getsockname()) for sock in server.sockets or [])
    compat_sockets = ", ".join(str(sock.getsockname()) for sock in compat_server.sockets or [])
    if ssl_ctx:
        log.info("DOIP server listening with TLS on %s", sockets)
        log.info("Compat JSON-segment listener with TLS on %s", compat_sockets)
    else:
        log.info("DOIP server listening (plaintext) on %s", sockets)
        log.info("Compat JSON-segment listener (plaintext) on %s", compat_sockets)

    async with server, compat_server:
        await asyncio.gather(server.serve_forever(), compat_server.serve_forever())


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        log.info("Server stopped by user")
