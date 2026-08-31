# EPISERVE DOIP Server

Asyncio-based DOIP 2.0 TCP server that exposes EPISERVE FAIR Digital Objects over the Digital Object Interface Protocol. Streams object components directly from lakeFS (`data-processed` and `model-runs` repositories) and integrates with a MediaWiki/Wikibase backend for derived items.

## Download the CLI binary

Pre-built binaries are published with every [GitHub Release](https://github.com/The-EPISERVE-Consortium/episerve_doip_server/releases).

## Ports

| Port | Protocol | Description |
|---|---|---|
| `3567` | TCP (binary DOIP) | Primary DOIP 2.0 listener (strict binary envelopes) |
| `3568` | TCP (JSON-segmented) | Compat listener for legacy `doipy`-style clients |
| `80` | HTTP | Gateway: `/doip/retrieve/{object_id}/{component_id}` |

## Supported operations

`hello`, `retrieve`, `update`, `invoke`, `list_ops`, `purge`

## Getting started

**Requirements:**

```bash
pip install -r requirements.txt
```

**Configuration** — either create `config.yaml` or set environment variables (env vars override the config file):

| Variable | Description |
|---|---|
| `LAKEFS_USER` | lakeFS username |
| `LAKEFS_PASSWORD` | lakeFS password |
| `LAKEFS_URL` | lakeFS endpoint, e.g. `https://lake-episerve.zib.de` |
| `LAKEFS_REPOS` | Comma-separated list of lakeFS repos to serve, e.g. `data-processed,model-runs` |
| `OLLAMA_API_KEY` | Ollama API key (optional, for invoke workflows) |

**Run the server:**

```bash
python -m doip_server.main           # binds 0.0.0.0:3567 (compat on 3568)
python -m doip_server.main --port 3567
```

## Getting started with Docker

The Docker image (`docker/Dockerfile`) bundles the server plus the HTTP gateway (nginx).

Build the image (from repo root):

```bash
docker build -f docker/Dockerfile -t episerve-doip .
```

Run:

```bash
docker run --rm \
  -p 80:80 -p 3567:3567 -p 3568:3568 \
  -e LAKEFS_URL=https://lake-episerve.zib.de \
  -e LAKEFS_USER=<user> -e LAKEFS_PASSWORD=<pass> \
  -e LAKEFS_REPOS=data-processed,model-runs \
  episerve-doip
```

HTTP gateway example (streams the component as a file download):

```bash
curl -OJ http://localhost/doip/retrieve/Q1748526042817/components/output/predictions.tsv
```

## Using the client CLI

Hello:

```bash
python -m client_cli.main --host localhost --port 3567 --action hello
```

Retrieve FDO metadata for a model run:

```bash
PYTHONPATH=. python -m client_cli.main --host localhost --no-tls --action retrieve \
  --object-id Q1748526042817
```

Retrieve a component (predictions file):

```bash
PYTHONPATH=. python -m client_cli.main --host localhost --no-tls --action retrieve \
  --object-id Q1748526042817 --component components/output/predictions.tsv \
  --output predictions.tsv
```

Update a component:

```bash
PYTHONPATH=. python -m client_cli.main --host localhost --no-tls --action update \
  --object-id Q1748526042817 --component components/output/predictions.tsv \
  --input predictions.tsv --media-type text/tab-separated-values
```

Component IDs are exact storage names — no extension is added automatically.

### Machine-readable output

Pass `--force-json-output` to make the CLI print exactly one JSON envelope on
stdout and nothing else (no banner, no debug logging; warnings and above still
go to stderr):

```bash
PYTHONPATH=. python -m client_cli.main --host localhost --no-tls \
  --force-json-output --action retrieve --object-id Q1748526042817
```

```json
{
  "action": "retrieve",
  "object_id": "Q1748526042817",
  "ok": true,
  "result": { "metadata_blocks": [ ... ] }
}
```

On failure the envelope is `{"action": ..., "object_id": ..., "ok": false, "error": "..."}`
and the exit code is `1`. In this mode a component retrieve (`--component`)
requires `--output` — binary content cannot share stdout with the envelope.
Supported actions: `hello`, `list_ops`, `retrieve`, `versions`, `update`,
`invoke`, `purge` (`demo` is interactive-only).

## Using the Python client

```python
from doip_client import StrictDOIPClient

client = StrictDOIPClient(host="doip.episerve.zib.de", port=3567, use_tls=False)
hello = client.hello()
metadata = client.retrieve("Q1748526042817").metadata_blocks
```

## TLS

Place `certs/server.crt` and `certs/server.key` (PEM) to enable TLS automatically. Without them the server speaks plaintext. The compat listener on port 3568 uses the same TLS setting.
