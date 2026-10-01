from __future__ import annotations

import asyncio
import json
import struct
from dataclasses import dataclass, field
from typing import List

from doip_shared.constants import (
    BLOCK_COMPONENT,
    BLOCK_METADATA,
    BLOCK_WORKFLOW,
    DOIP_VERSION,
    MSG_TYPE_ERROR,
    MSG_TYPE_REQUEST,
    MSG_TYPE_RESPONSE,
    OP_HELLO,
    OP_INVOKE,
    OP_LIST_OPS,
    OP_RETRIEVE,
    OP_UPDATE,
    OP_DESCRIBE,
    OP_PURGE,
)

HEADER_STRUCT = struct.Struct(">BBBBHI")
HEADER_SIZE = HEADER_STRUCT.size


class ProtocolError(Exception):
    """Raised when a DOIP envelope is malformed."""


class AccessDeniedError(ProtocolError):
    """Raised when a request lacks the credential required by a restricted object.

    The error envelope carries the class name, which lets the HTTP gateway map
    it to 401/403 instead of a generic failure.
    """


@dataclass
class ComponentBlock:
    """Binary component block inside a DOIP payload."""

    component_id: str
    content: bytes
    media_type: str = "application/octet-stream"
    declared_size: int | None = None


@dataclass
class DOIPMessage:
    """Represents a parsed or to-be-encoded DOIP message envelope."""

    version: int
    msg_type: int
    operation: int
    flags: int
    object_id: str
    metadata_blocks: List[dict] = field(default_factory=list)
    component_blocks: List[ComponentBlock] = field(default_factory=list)
    workflow_blocks: List[dict] = field(default_factory=list)

    def to_bytes(self) -> bytes:
        """Encode this DOIPMessage into its wire binary representation.

        Returns:
            bytes: Serialized DOIP envelope including header and payload blocks.
        """
        payload_chunks: List[bytes] = []
        for block in self.metadata_blocks:
            payload_chunks.append(encode_metadata_block(block))
        for block in self.component_blocks:
            payload_chunks.append(encode_component_block(block))
        for block in self.workflow_blocks:
            payload_chunks.append(encode_workflow_block(block))
        payload = b"".join(payload_chunks)
        obj_bytes = self.object_id.encode("utf-8")
        header = HEADER_STRUCT.pack(
            self.version,
            self.msg_type,
            self.operation,
            self.flags,
            len(obj_bytes),
            len(payload),
        )
        return header + obj_bytes + payload


def encode_metadata_block(data: dict) -> bytes:
    """Encode a metadata block as JSON with type prefix and length.

    Args:
        data: Metadata dictionary to serialize.

    Returns:
        bytes: Encoded block with header and body.
    """
    body = json.dumps(data, separators=(",", ":"), ensure_ascii=False).encode("utf-8")
    length = struct.pack(">BI", BLOCK_METADATA, len(body))
    return length + body


def encode_workflow_block(data: dict) -> bytes:
    """Encode a workflow block as JSON with type prefix and length.

    Args:
        data: Workflow result or request metadata.

    Returns:
        bytes: Encoded workflow block.
    """
    body = json.dumps(data, separators=(",", ":"), ensure_ascii=False).encode("utf-8")
    length = struct.pack(">BI", BLOCK_WORKFLOW, len(body))
    return length + body


def encode_component_block(block: ComponentBlock) -> bytes:
    """Encode a component block with IDs, media type, and raw bytes.

    Args:
        block: ComponentBlock to serialize.

    Returns:
        bytes: Encoded component block with framing.
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
    length = struct.pack(">BI", BLOCK_COMPONENT, len(body))
    return length + body


async def read_doip_message(reader: asyncio.StreamReader) -> DOIPMessage:
    """Read and parse a DOIP message from an asyncio stream.

    Args:
        reader: StreamReader positioned at the start of a DOIP envelope.

    Returns:
        DOIPMessage: Parsed message with header and blocks.

    Raises:
        ProtocolError: When envelope is malformed or unsupported.
    """
    header_bytes = await reader.readexactly(HEADER_SIZE)
    version, msg_type, operation, flags, object_id_len, payload_len = HEADER_STRUCT.unpack(
        header_bytes
    )
    if version != DOIP_VERSION:
        raise ProtocolError(f"Unsupported DOIP version {version}")
    object_id_bytes = await reader.readexactly(object_id_len)
    object_id = object_id_bytes.decode("utf-8")
    payload = await reader.readexactly(payload_len)
    metadata_blocks: List[dict] = []
    component_blocks: List[ComponentBlock] = []
    workflow_blocks: List[dict] = []

    offset = 0
    while offset < len(payload):
        if offset + 5 > len(payload):
            raise ProtocolError("Truncated DOIP block header")
        block_type = payload[offset]
        block_len = struct.unpack_from(">I", payload, offset + 1)[0]
        offset += 5
        end = offset + block_len
        if end > len(payload):
            raise ProtocolError("Truncated DOIP block body")
        block_body = payload[offset:end]
        offset = end

        if block_type == BLOCK_METADATA:
            metadata_blocks.append(json.loads(block_body.decode("utf-8")))
        elif block_type == BLOCK_WORKFLOW:
            workflow_blocks.append(json.loads(block_body.decode("utf-8")))
        elif block_type == BLOCK_COMPONENT:
            component_blocks.append(_decode_component_block(block_body))
        else:
            raise ProtocolError(f"Unknown block type {block_type}")

    return DOIPMessage(
        version=version,
        msg_type=msg_type,
        operation=operation,
        flags=flags,
        object_id=object_id,
        metadata_blocks=metadata_blocks,
        component_blocks=component_blocks,
        workflow_blocks=workflow_blocks,
    )


def _decode_component_block(body: bytes) -> ComponentBlock:
    """Decode a component block body into a ComponentBlock.

    Args:
        body: Raw component block content after type/length.

    Returns:
        ComponentBlock: Parsed component information and data.

    Raises:
        ProtocolError: When block is truncated or inconsistent.
    """
    if len(body) < 8:
        raise ProtocolError("Component block too small")
    offset = 0
    comp_id_len = struct.unpack_from(">H", body, offset)[0]
    offset += 2
    comp_id = body[offset : offset + comp_id_len].decode("utf-8")
    offset += comp_id_len
    media_len = struct.unpack_from(">H", body, offset)[0]
    offset += 2
    media_type = body[offset : offset + media_len].decode("utf-8")
    offset += media_len
    content_len = struct.unpack_from(">I", body, offset)[0]
    offset += 4
    content = body[offset : offset + content_len]
    if len(content) != content_len:
        raise ProtocolError("Component content length mismatch")
    return ComponentBlock(
        component_id=comp_id, content=content, media_type=media_type or "application/octet-stream", declared_size=content_len
    )
