# Configuration

The server builds its configuration by merging **`config.yaml` → environment variables → CLI flags**. This lets you keep sane defaults in `config.yaml`, override secrets with env vars, and make temporary changes with flags like `--fdo-api`.

## Ports and listeners
- Binary DOIP listener: defaults to `3567` (set with `--port`).
- Compatibility JSON-segment listener: always runs on `port + 1` (default `3568`).
- TLS is enabled automatically when both `certs/server.crt` and `certs/server.key` exist. Otherwise the listeners stay plaintext.

## Supported environment variables
| Variable | Purpose |
| --- | --- |
| `FDO_API` | Base URL of the FDO façade (e.g., `https://fdo.portal.mardi4nfdi.de/fdo/`). Overrides any value in `config.yaml` or `--fdo-api`. |
| `LAKEFS_URL` | lakeFS endpoint, with or without protocol prefix. Normalized to https when missing. |
| `LAKEFS_REPO` | lakeFS repository name used for component lookup. |
| `LAKEFS_USER` | lakeFS access key. |
| `LAKEFS_PASSWORD` | lakeFS secret key. This value is also used as the shared secret for DOIP `update` authorization. |
| `OLLAMA_API_KEY` | API key passed to the Ollama client when invoking workflows. |

When set, these variables override matching keys inside `config.yaml`.

## `config.yaml` layout (example)
```yaml
# Simplified template – replace with your endpoints and credentials
ollama:
  host: localhost
  port: 11434
  use_ssl: false
  api_key: "${OLLAMA_API_KEY:-}"
  standard_model: qwen2:1.5b
  timeout: 2
lakefs:
  url: lake-bioinfmed.zib.de
  repo: sandbox
  signature_version: s3v4
  user: "${LAKEFS_USER:-}"
  password: "${LAKEFS_PASSWORD:-}"
```
Keep secrets in env vars rather than committing them to the template.

## Restricted objects
An object whose FDO has `profile.accessRights: restricted` is served only to requests that present the read token: `DOIP_READ_TOKEN` (or `access.read_token` in `config.yaml`) when set, otherwise the update token, which is the lakeFS password. Anyone holding that token can also run `update`, so set a separate `DOIP_READ_TOKEN` to hand out read-only access. The check covers component retrieves (including older `version`s), the `rocrate` element and `invoke`; FDO metadata and the version list stay readable. If neither is available, restricted objects are refused for everyone.

- HTTP gateway: send `Authorization: Bearer <token>`. A missing token returns `401`, a wrong one `403`.
- Native protocol: put `"token"` in the request's metadata block (`StrictDOIPClient.retrieve(..., token=...)`).
- CLI: `--read-token`, else `DOIP_READ_TOKEN`, else `DOIP_UPDATE_TOKEN`.

**Signed links.** Browsers cannot send a token (e.g. the CKAN parquet preview), so a component of a restricted object can also be requested with `?exp=<unix seconds>&sig=<signature>`. The signature is `HMAC-SHA256(DOIP_LINK_SECRET, "<QID>\n<component>\n<exp>")` in hex (see `doip_shared/signing.py`) and is valid for that object and component only, until `exp`. It works for component retrieves (GET and HEAD), not for `rocrate` or `invoke`. `DOIP_LINK_SECRET` is separate from the update token: it can only create read links. A missing or wrong signature gives `403`; unset secret means signed links are refused.

The client library never reads `DOIP_READ_TOKEN` itself: the gateway runs next to the server, so a fallback would authorize every request.

## CLI flags
- `--port`: TCP port for the binary listener (compatibility listener uses `port+1`).
- `--fdo-api`: Overrides the FDO façade URL for a single run.

Example: start TLS listeners on custom ports using env overrides:
```bash
export FDO_API="https://fdo.example.org/fdo/"
export LAKEFS_URL="https://lakefs.internal"
export LAKEFS_USER="admin" LAKEFS_PASSWORD="***"
python -m doip_server.main --port 4567 --fdo-api "$FDO_API"
```
