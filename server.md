# Server

## Overview
Asyncio-based TCP server implementing strict DOIP v2.0 framing. Supported operations:
- Hello (`0x01`)
- Retrieve (`0x02`)
- Invoke (`0x05`)
- List operations helper (`list_ops`)
- Purge (`0x07`)

Two listeners start together:
- Binary DOIP: default `3567` (set with `--port`).
- Compatibility JSON-segment listener: `port + 1` (default `3568`) for doipy-style clients.
If `certs/server.crt` and `certs/server.key` exist, both listeners start with TLS; otherwise they stay plaintext.

## Entrypoint
Module `doip_server.main` exposes the CLI and event loop bootstrap.

Start the server (plaintext):
```bash
PYTHONPATH=. python -m doip_server.main --port 3567
```

CLI flags:
- `--port`: TCP port for the binary listener.
- `--fdo-api`: Override the FDO façade base URL for the current run.

Configuration order of precedence: `config.yaml` → environment variables (`FDO_API`, `LAKEFS_*`, `OLLAMA_API_KEY`) → CLI flags. See **Configuration** for details.

## Handlers
- `handle_hello`
  - **Motivation**: Allow clients to verify connectivity and discover supported operations without performing data transfers.
  - **Use case**: A monitoring probe or client bootstrap issues `hello` to confirm the endpoint is alive and reads the `availableOperations` map.
- `handle_retrieve`
  - **Motivation**: Deliver FAIR Digital Object bitstreams/components via strict DOIP framing.
  - **Use case**: A client requests `doip:bitstream/Q123/main-pdf` to download the canonical PDF for object `Q123`; the handler fetches the bytes from storage and streams component blocks back.
- `handle_invoke`
  - **Motivation**: Trigger server-side workflows that derive new components or metadata from an object.
  - **Use case**: A client invokes the `equation_extraction` workflow on `Q123` to produce a JSON of extracted equations and receive the derived component and workflow result metadata.
- `handle_list_ops`
  - **Motivation**: Advertise supported operations for discovery.
  - **Use case**: The client calls `list_ops` to prime its allowed call set.
- `handle_purge`
  - **Motivation**: Allow clients to force a fresh manifest fetch without restarting the server.
  - **Use case**: An operator calls `purge` for a specific PID after updating its FDO metadata, ensuring the next retrieve reflects the latest state.

## Protocol
- Header: `>BBBBHI` (version, msg type, operation, flags, object ID length, payload length).
- Blocks: metadata, component, workflow.

See `doip_server/protocol.py` for framing details.

## Compatibility listener
The companion listener on `port + 1` accepts doipy-style length-prefixed JSON segments, converts them to DOIP requests, and streams back segments. The first segment is a JSON status block followed by optional component payloads.
