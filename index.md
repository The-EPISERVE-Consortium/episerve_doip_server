# Mardi DOIP Server & Client

Strict DOIP v2.0 implementation for FAIR Digital Objects (FDOs), backed by lakeFS storage and an FDO façade. This site covers how to run the server, call it from Python or the CLI, configure TLS, and build the Docker image.

## Why DOIP and FDO?
- **FAIR Digital Objects (FDOs)** provide persistent identifiers plus structured metadata and component links, improving interoperability and long-term access.
- **Digital Object Interface Protocol (DOIP)** standardizes how to fetch and operate on those objects over the network using binary envelopes and operation codes.
- This project supplies both sides—server and client—so MaRDI services can publish and consume FDO content (bitstreams, derived components, workflow results) consistently.

## Capabilities at a glance
- Binary DOIP listener on TCP (`3567` by default) with automatic TLS when `certs/server.crt` and `certs/server.key` exist.
- Compatibility JSON-segment listener on `port + 1` (`3568` by default) for doipy-style clients.
- Operations: `hello`, `retrieve`, `invoke`, plus a `list_ops` helper.
- lakeFS-backed component retrieval and workflow-driven derived components.

## Quick start
1) Start the server (plaintext example):
```bash
PYTHONPATH=. python -m doip_server.main --port 3567
```
2) Retrieve an object with the CLI:
```bash
PYTHONPATH=. python -m client_cli.main \
  --host 127.0.0.1 --port 3567 --no-tls \
  --action retrieve --object-id Q6190920 --output .
```
The CLI issues `hello` then `retrieve`, prints returned metadata, and saves the first component using the server-provided filename when available.

See **Configuration** to point the server at your lakeFS and FDO endpoints, and **Docker** for containerized runs.
