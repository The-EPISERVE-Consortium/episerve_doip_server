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
