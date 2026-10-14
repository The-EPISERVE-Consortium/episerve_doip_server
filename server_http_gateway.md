# Server HTTP Gateway

This component provides a thin HTTP layer that forwards browser-friendly download requests to the internal DOIP server. It is served by FastAPI in `doip_server/http_gateway.py` and is intended for environments where a simple REST-style endpoint is preferred over the native DOIP protocol.

## Endpoints

- `GET /doip/retrieve/{object_id}/{component_id}`
  - Streams the first matching component block for the given object/component pair.
  - Sets `Content-Type` from the component's `media_type` (defaults to `application/octet-stream`).
  - Adds `Content-Disposition: attachment; filename="<component_id_basename>"` so browsers download instead of render.
  - Returns `404` if no component blocks are present; `502` for backend failures.
  - Accepts an optional `?force_reload` query parameter. When present, the server-side manifest cache for the object is purged before the component is fetched, ensuring fresh data is returned.
  - Supports partial downloads, so large components (for example Parquet files) never have to be read completely. Only the requested bytes are read from lakeFS (an S3 `Range` request):
    - `Range: bytes=a-b`, `bytes=a-` and the suffix form `bytes=-n` are answered with `206 Partial Content` and a `Content-Range: bytes start-end/total` header. A range that starts beyond the end of the component gets `416` with `Content-Range: bytes */total`.
    - `?size=N` returns the first `N` bytes as `206` (or the whole component with `200` when it is not longer than `N`). If a request has both `?size` and a `Range` header, `size` wins.
    - Multiple ranges, malformed headers and `bytes=0-` are served as the whole component.
    - Without `size` or `Range`, the whole component is served as before.
    - `exp`/`sig` (signed links) and Bearer tokens work the same way for partial requests.

- `HEAD /doip/retrieve/{object_id}/{component_id}`
  - Returns the same headers as `GET` without a body, including the real `Content-Length` of the component. The size is read from a one-byte request, so the component is not loaded. Clients such as the CKAN Parquet viewer use this before issuing `Range` requests.

- `POST /doip/purge/{object_id}`
  - Evicts the cached manifest for `object_id` from the DOIP server's in-memory cache.
  - Subsequent requests for that object will re-fetch the manifest from the FDO API.
  - Returns a JSON confirmation: `{"status": "purged", "pid": "<OBJECT_ID>"}`.
  - Returns `502` on backend failures.

### Examples

```bash
# Download a component (cached)
curl -OJ http://localhost/doip/retrieve/Q6033164/fulltext

# Download a component, bypassing the cache
curl -OJ "http://localhost/doip/retrieve/Q6033164/fulltext?force_reload"

# First 1 KiB of a component (206, Content-Range shows the total size)
curl -s -D - -o head.bin "http://localhost/doip/retrieve/Q6033164/fulltext?size=1024"

# Bytes 1000-1999, and the last 8 bytes (e.g. a Parquet footer)
curl -r 1000-1999 -o part.bin http://localhost/doip/retrieve/Q6033164/fulltext
curl -r -8 -o tail.bin http://localhost/doip/retrieve/Q6033164/fulltext

# Purge the cache for an object without downloading
curl -X POST http://localhost/doip/purge/Q6033164
```

## Backend connection

The gateway talks to the DOIP binary server via `StrictDOIPClient`. Host/port are resolved in this order:

- `DOIP_BACKEND_HOST` / `DOIP_BACKEND_PORT`: explicit backend target (use when gateway and server are in different pods/services).
- `DOIP_HOST` / `DOIP_PORT`: legacy variables for compatibility.
- Default: `127.0.0.1:3567`.

Safety fallback: if the resolved backend port is `80` (the gateway’s own port), the gateway logs a warning and automatically falls back to `3567` to avoid loopback TLS errors.

- `DOIP_VERIFY_TLS` (default `false`): set to `true` to enable certificate verification when TLS is active.
- TLS is automatically enabled when `certs/server.crt` exists alongside the gateway image; override by removing the cert or setting `DOIP_USE_TLS=false`.

## Static assets

The root path `/` serves the legacy landing page and associated assets from `/app/landing` using FastAPI's `StaticFiles` mount. This keeps the former download UI available without the DOIP protocol in the browser.

## Running locally

The Docker entrypoint starts the gateway with Uvicorn (alongside the main server):

```bash
uvicorn doip_server.http_gateway:app --host 0.0.0.0 --port 80
```

For development you can run the same command from the repository root (inside an activated virtualenv) and then issue the `curl` example above.
