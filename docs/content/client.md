# Client

`doip_client` implements a strict DOIP v2.0 client that mirrors the server framing (binary envelope, metadata/component/workflow blocks) and supports TLS.

## Core operations
- `hello()`: Health check and capability discovery.
- `list_ops()`: Fetch the `availableOperations` map.
- `retrieve(object_id, component_id=None, version=None, ..., size=None, offset=None)`: Return metadata blocks or a specific component. With `size` and/or `offset` only that byte range of the component is read from storage (see below).
- `update_component(object_id, component_id, content, media_type=...)`: Update one component on an existing object and trigger a lakeFS commit.
- `invoke(object_id, workflow, params=None)`: Trigger a workflow; receives workflow metadata and derived components.

## Usage
```python
from doip_client import StrictDOIPClient

client = StrictDOIPClient(host="127.0.0.1", port=3567, use_tls=False)
hello = client.hello()
ops = client.list_ops()
metadata = client.retrieve("Q123").metadata_blocks
pdf = client.retrieve("Q123", component="doip:bitstream/Q123/main-pdf")
update = client.update_component("Q123", "fulltext.pdf", b"new-pdf-bytes", media_type="application/pdf")

# Partial retrieve: only the requested bytes are read from storage
r = client.retrieve("Q123", "data.parquet", size=1024)               # first 1 KiB
r = client.retrieve("Q123", "data.parquet", offset=8192, size=4096)  # 4 KiB from byte 8192
r = client.retrieve("Q123", "data.parquet", offset=-8)               # last 8 bytes
r.component_blocks[0].content       # the bytes
r.metadata_blocks[0]                # {"offset": ..., "totalSize": ..., "partial": True, ...}

# Invoke a workflow with parameters
result = client.invoke("Q123", workflow="equation_extraction", params={"pages": [1, 2, 3]})
```

### Partial retrieves
`size` is a positive number of bytes. `offset` is the first byte; a negative `offset` counts from the end and cannot be combined with `size`. Without `size`, `offset` reads to the end of the component. Without either, the whole component is returned as before.

The response carries a metadata block with `offset` (first byte returned), `totalSize` (size of the whole component) and `partial`. If `offset` is beyond the end, there is no component block and the metadata block has `rangeNotSatisfiable: true` together with `totalSize`.

### TLS & verification
Pass `use_tls=True` to wrap the socket. If you use self-signed certs during development, combine `use_tls=True` with `verify=False` to skip hostname verification.

### Timeouts
The client uses blocking sockets; wrap calls in your own timeout logic if needed.

## Component handling
- For metadata-only requests, send no `component` and inspect `response.metadata_blocks`.
- For binaries, pass the component ID; the client returns `ComponentBlock` objects containing `component_id`, `media_type`, and `content` bytes.
- For updates, send exactly one component block. Other existing components remain unchanged.
- Component IDs are exact names. The server does not add or infer extensions for storage paths.

## Compatibility listener support
The Python client and the **Client CLI** both speak strict binary DOIP only. Neither talks to the compatibility listener (`port + 1`), and that listener's custom length-prefixed framing is not compatible with `doipy`. See **Server → Compatibility listener**.
