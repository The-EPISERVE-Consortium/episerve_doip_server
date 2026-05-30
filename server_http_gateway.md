# Server HTTP Gateway

This component provides a thin HTTP layer that forwards browser-friendly download requests to the internal DOIP server. It is served by FastAPI in `doip_server/http_gateway.py` and is intended for environments where a simple REST-style endpoint is preferred over the native DOIP protocol.

## Endpoints

- `GET /doip/retrieve/{object_id}/{component_id}`
  - Streams the first matching component block for the given object/component pair.
  - Sets `Content-Type` from the component's `media_type` (defaults to `application/octet-stream`).
  - Adds `Content-Disposition: attachment; filename="<component_id_basename>"` so browsers download instead of render.
  - Returns `404` if no component blocks are present; `502` for backend failures.
  - Accepts an optional `?force_reload` query parameter. When present, the server-side manifest cache for the object is purged before the component is fetched, ensuring fresh data is returned.

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
