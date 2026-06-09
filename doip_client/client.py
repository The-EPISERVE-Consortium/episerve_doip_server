"""Strict DOIP v2.0 client implementation."""

from __future__ import annotations

import os
import socket
import struct
import ssl
from pathlib import Path

from doip_shared.constants import OP_HELLO, OP_INVOKE, OP_LIST_OPS, OP_PURGE, OP_RETRIEVE, OP_UPDATE

from . import protocol, tls, utils
from .logging_config import log
from .messages import ComponentBlock, DoipRequest, DoipResponse
from .protocol import (
    BLOCK_COMPONENT,
    BLOCK_METADATA,
    BLOCK_WORKFLOW,
    DOIP_VERSION,
    HEADER_STRUCT,
    MSG_TYPE_REQUEST,
    encode_doip_block,
    decode_doip_blocks,
    decode_header,
    Header,
)


class StrictDOIPClient:
    """Blocking TCP/TLS DOIP v2.0 client."""

    def __init__(self, host: str, port: int, use_tls: bool = True, verify_tls: bool = True, timeout: int = 30):
        """Initialize the client.

        Args:
            host: Server hostname or IP.
            port: Server port.
            use_tls: Wrap connection with TLS if True.
            verify_tls: Verify server certificate/hostname when TLS is enabled.
            timeout: Socket timeout in seconds.
        """
        self.host = host
        self.port = port
        self.use_tls = use_tls
        self.verify_tls = verify_tls
        self.timeout = timeout

    def hello(self) -> dict:
        """Perform the DOIP hello operation and return response metadata.

        Returns:
            Metadata dictionary from the server.
        """
        log.info("hello() - use-tls=%s", self.use_tls)

        if self.use_tls:
            ca_paths = ssl.get_default_verify_paths()
            certs_available = self._certs_available(ca_paths)
            log.info(
                "TLS enabled; verify=%s; certs_available=%s; cafile=%s; capath=%s",
                self.verify_tls,
                certs_available,
                ca_paths.cafile,
                ca_paths.capath,
            )

        request = DoipRequest(
            header=Header(DOIP_VERSION, MSG_TYPE_REQUEST, OP_HELLO, 0, 0, 0),
            object_id="",
            metadata_blocks=[{"operation": "hello"}],
        )
        response = self.send_message(request)
        return response.metadata_blocks[0] if response.metadata_blocks else {}

    def list_ops(self) -> dict:
        """Request the list of supported operations from the server.

        Returns:
            Metadata dictionary describing available operations.
        """
        request = DoipRequest(
            header=Header(DOIP_VERSION, MSG_TYPE_REQUEST, OP_LIST_OPS, 0, 0, 0),
            object_id="",
            metadata_blocks=[{"operation": "list_operations"}],
        )
        response = self.send_message(request)
        return response.metadata_blocks[0] if response.metadata_blocks else {}

    def retrieve(self, object_id: str, component_id: str = None, version: str | None = None, limit: int | None = None, include_sizes: bool = False) -> DoipResponse:
        """Retrieve the primary payload for a given object ID.

        Args:
            object_id: Target object identifier.
            component_id: Component identifier or None.
            version: Commit ID to fetch from, or None/"latest" for the current branch.
            limit: Maximum number of versions to return (versions element only).
            include_sizes: When True, include size_bytes in each version entry.

        Returns:
            Parsed DOIP response envelope.
        """
        meta = {"operation": "retrieve"}
        if component_id:
            meta["element"] = component_id
        if version and version != "latest":
            meta["version"] = version
        if limit is not None:
            meta["limit"] = limit
        if include_sizes:
            meta["include_sizes"] = True

        log.info("retrieve() for object_id=%s and element=%s", object_id, component_id)

        request = DoipRequest(
            header=Header(DOIP_VERSION, MSG_TYPE_REQUEST, OP_RETRIEVE, 0, 0, 0),
            object_id=object_id,
            metadata_blocks=[meta],
        )
        return self.send_message(request)

    def retrieve_component(self, qid: str, component_id: str) -> DoipResponse:
        """Convenience wrapper for retrieving a specific component."""
        return self.retrieve(qid, component_id)


    def purge(self, object_id: str) -> dict:
        """Purge the server-side manifest cache for a given object ID.

        Args:
            object_id: PID/QID whose cache entry should be evicted.

        Returns:
            Metadata dictionary from the server confirming the purge.
        """
        request = DoipRequest(
            header=Header(DOIP_VERSION, MSG_TYPE_REQUEST, OP_PURGE, 0, 0, 0),
            object_id=object_id,
            metadata_blocks=[{"operation": "purge"}],
        )
        response = self.send_message(request)
        return response.metadata_blocks[0] if response.metadata_blocks else {}

    def update_component(
        self,
        object_id: str,
        component_id: str,
        content: bytes,
        media_type: str = "application/octet-stream",
        update_token: str | None = None,
    ) -> DoipResponse:
        """Update one component on an existing object.

        Args:
            object_id: Target object identifier.
            component_id: Component identifier to create or replace.
            content: Component bytes to upload.
            media_type: MIME type recorded for the uploaded component.
            update_token: Optional shared secret for authorizing the update.

        Returns:
            DoipResponse: Parsed DOIP response envelope.
        """
        resolved_token = self._resolve_update_token(update_token)
        metadata = {"operation": "update", "element": component_id}
        if resolved_token:
            metadata["token"] = resolved_token

        request = DoipRequest(
            header=Header(DOIP_VERSION, MSG_TYPE_REQUEST, OP_UPDATE, 0, 0, 0),
            object_id=object_id,
            metadata_blocks=[metadata],
            component_blocks=[
                ComponentBlock(
                    component_id=component_id,
                    content=content,
                    media_type=media_type,
                )
            ],
        )
        return self.send_message(request)

    @staticmethod
    def _resolve_update_token(update_token: str | None) -> str | None:
        """Resolve the update token from explicit input or the environment.

        Args:
            update_token: Explicit token supplied by the caller.

        Returns:
            str | None: Explicit token when provided, otherwise
                ``DOIP_UPDATE_TOKEN`` from the environment.
        """
        if update_token:
            return update_token
        env_token = os.getenv("DOIP_UPDATE_TOKEN")
        return env_token or None

    def invoke(self, object_id: str, workflow: str, params: dict | None = None) -> DoipResponse:
        """Invoke a workflow on the server for a given object ID.

        Args:
            object_id: Target object identifier.
            workflow: Workflow name to run.
            params: Optional workflow parameters.

        Returns:
            Parsed DOIP response envelope.
        """
        metadata = {"operation": "invoke", "workflow": workflow, "params": params or {}}
        request = DoipRequest(
            header=Header(DOIP_VERSION, MSG_TYPE_REQUEST, OP_INVOKE, 0, 0, 0),
            object_id=object_id,
            metadata_blocks=[metadata],
        )
        return self.send_message(request)

    def send_message(self, request: DoipRequest) -> DoipResponse:
        """Send a strict DOIP request and parse the response.

        Args:
            request: Fully constructed DOIP request envelope.

        Returns:
            Parsed DOIP response envelope.

        Raises:
            ConnectionError: If the socket closes unexpectedly.
            ValueError: If framing is invalid.
        """
        object_id_bytes = request.object_id.encode("utf-8")
        payload_parts: list[bytes] = []

        for meta in request.metadata_blocks:
            payload_parts.append(encode_doip_block(BLOCK_METADATA, utils.dict_to_json_bytes(meta)))
        for comp in request.component_blocks:
            payload_parts.append(encode_doip_block(BLOCK_COMPONENT, self._encode_component_body(comp)))
        for wf in request.workflow_blocks:
            payload_parts.append(encode_doip_block(BLOCK_WORKFLOW, utils.dict_to_json_bytes(wf)))

        payload = b"".join(payload_parts)
        payload_len = len(payload)

        header_bytes = HEADER_STRUCT.pack(
            DOIP_VERSION,
            MSG_TYPE_REQUEST,
            request.header.op_code,
            request.header.flags,
            len(object_id_bytes),
            payload_len,
        )
        message = header_bytes + object_id_bytes + payload

        try:
            sock = socket.create_connection((self.host, self.port), timeout=self.timeout)
        except OSError as exc:
            raise ConnectionError(
                f"Failed to connect to {self.host}:{self.port} "
                f"(tls={self.use_tls}, verify_tls={self.verify_tls}, timeout={self.timeout}s): {exc}"
            ) from exc
        sock = tls.wrap_socket(sock, self.host, self.use_tls, self.verify_tls)
        try:
            sock.sendall(message)

            resp_header_bytes = self._recv_exact(sock, protocol.HEADER_LENGTH)
            resp_header = decode_header(resp_header_bytes)

            object_id = self._recv_exact(sock, resp_header.object_id_len)
            payload_bytes = self._recv_exact(sock, resp_header.payload_len)

            metadata_blocks, component_blocks, workflow_blocks = decode_doip_blocks(payload_bytes)

            return DoipResponse(
                header=resp_header,
                metadata_blocks=metadata_blocks,
                component_blocks=component_blocks,
                workflow_blocks=workflow_blocks,
            )
        finally:
            sock.close()

    @staticmethod
    def _encode_component_body(block: ComponentBlock) -> bytes:
        """Encode a component block body (without type/length).

        Args:
            block: Component block to encode.

        Returns:
            Encoded component body bytes.
        """
        comp_id_bytes = block.component_id.encode("utf-8")
        media_bytes = (block.media_type or "").encode("utf-8")
        content = block.content
        body = b"".join(
            [
                struct.pack(">H", len(comp_id_bytes)),
                comp_id_bytes,
                struct.pack(">H", len(media_bytes)),
                media_bytes,
                struct.pack(">I", len(content)),
                content,
            ]
        )
        return body

    @staticmethod
    def _recv_exact(sock: socket.socket, size: int) -> bytes:
        """Receive exactly size bytes from the socket.

        Args:
            sock: Socket to read from.
            size: Number of bytes to read.

        Returns:
            Bytes read from the socket.

        Raises:
            ConnectionError: If the socket closes early.
        """
        chunks: list[bytes] = []
        remaining = size
        while remaining > 0:
            chunk = sock.recv(remaining)
            if not chunk:
                raise ConnectionError("Socket closed before receiving expected bytes")
            chunks.append(chunk)
            remaining -= len(chunk)
        return b"".join(chunks)

    @staticmethod
    def _certs_available(paths: ssl.DefaultVerifyPaths) -> bool:
        """Return True when a system CA file or directory exists.

        Args:
            paths: Default verification paths derived from the SSL module.

        Returns:
            bool: True if either ``cafile`` or ``capath`` exists.
        """
        cafile_exists = bool(paths.cafile and Path(paths.cafile).exists())
        capath_exists = bool(paths.capath and Path(paths.capath).exists())
        return cafile_exists or capath_exists

    @staticmethod
    def save_first_component(response: DoipResponse, output_path: str | None = None) -> str:
        """Save the first component block to disk and return the path.

        If the server supplies an originalFilename in metadata, that name is used;
        otherwise falls back to output_path or component_id basename.

        Args:
            response: DOIP response containing component blocks.
            output_path: Optional target path; if a directory, the file is placed there.

        Returns:
            str: Path to the saved file.

        Raises:
            ValueError: If no component blocks are present.
            OSError: If writing the file fails.
        """
        import os
        from pathlib import Path

        if not response.component_blocks:
            raise ValueError("No component blocks to save")
        comp = response.component_blocks[0]
        target_name = Path(comp.component_id).name

        if output_path:
            output_path = str(output_path)
            if os.path.isdir(output_path):
                dest = Path(output_path) / target_name
            else:
                dest = Path(output_path)
        else:
            dest = Path(target_name)

        dest.write_bytes(comp.content)
        return str(dest)
