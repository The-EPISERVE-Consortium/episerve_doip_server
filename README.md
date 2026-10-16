# EPISERVE DOIP Server

Asyncio-based DOIP 2.0 TCP server that exposes EPISERVE FAIR Digital Objects over the Digital Object Interface Protocol. Streams object components directly from lakeFS (`data-processed` and `model-runs` repositories) and integrates with a MediaWiki/Wikibase backend for derived items.

## Download the CLI binary

Pre-built binaries are published with every [GitHub Release](https://github.com/The-EPISERVE-Consortium/episerve_doip_server/releases).

## Ports

| Port | Protocol | Description |
|---|---|---|
| `3567` | TCP (binary DOIP) | Primary DOIP 2.0 listener (strict binary envelopes) |
| `3568` | TCP (length-prefixed JSON) | Compat listener with custom framing. **Not** compatible with `doipy` (see below). Not exposed on the EPISERVE deployment. |
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
| `DOIP_READ_TOKEN` | Optional. Secret that grants access to *restricted* objects (FDO `profile.accessRights == "restricted"`). If unset, the update token (the lakeFS password) is used. |
| `DOIP_LINK_SECRET` | Optional. Secret for verifying expiring signed links (`?exp=…&sig=…`) to restricted components, shared with whoever creates the links (the CKAN theme). Unset = signed links are refused. |
| `DOIP_MANIFEST_CACHE_TTL` | Seconds an object's FDO stays cached (default `300`; `0` = never expire). A change to the FDO, e.g. `accessRights`, takes effect after at most this long. |
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

The gateway also serves partial content, reading only the requested bytes from lakeFS:

```bash
curl -r 0-1023 http://localhost/doip/retrieve/Q1748526042817/components/output/predictions.tsv   # Range request (206)
curl "http://localhost/doip/retrieve/Q1748526042817/components/output/predictions.tsv?size=1024"  # first 1 KiB
curl -I http://localhost/doip/retrieve/Q1748526042817/components/output/predictions.tsv          # size without loading the file
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

Retrieve only part of a component (`--size` bytes from `--offset`; a negative offset counts from the end):

```bash
PYTHONPATH=. python -m client_cli.main --host localhost --no-tls --action retrieve \
  --object-id Q1748526042817 --component components/output/predictions.tsv \
  --size 4096 --output head.tsv
```

Update a component:

```bash
PYTHONPATH=. python -m client_cli.main --host localhost --no-tls --action update \
  --object-id Q1748526042817 --component components/output/predictions.tsv \
  --input predictions.tsv --media-type text/tab-separated-values
```

Component IDs are exact storage names — no extension is added automatically.

### Version and update check

Released binaries are stamped with their git tag at build time, so the client
knows its own version:

```bash
episerve-doip-cli --cli-version
episerve-doip-cli 0.0.6
```

The version is printed to stdout; the update status goes to stderr. Exit codes:
`0` up to date, `1` a newer release exists, `2` the lookup failed. The version
is reported even when the lookup fails. If the repository cannot be reached,
the status line is simply `Cannot reach github.com to check for updates.` and
the version is still printed.

```bash
# usable in a script or CI gate
if episerve-doip-cli --cli-version; then echo current; fi
```

When started interactively, the client also checks once a day whether a newer
release exists and prints a notice on stderr (never stdout, so scripted and
`--force-json-output` use is unaffected). Prereleases (`rc` tags) are never
offered, and the check is skipped entirely for development builds
(`0.0.0.dev0`), in CI, and when stderr is not a terminal.

```bash
episerve-doip-cli --no-cli-update-check  # skip the startup check for this run
EPISERVE_NO_CLI_UPDATE_CHECK=1 episerve-doip-cli --action hello
```

The check queries the repository's git ref endpoint (the one `git ls-remote`
uses), which needs no token and is not subject to the GitHub API rate limit.
It is best-effort: a missing network, a timeout or an unparseable response all
count as "no update" and never affect the command you actually ran. The result
is cached for 24 h under `~/.cache/episerve/` (`%LOCALAPPDATA%` on Windows).

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

client = StrictDOIPClient(host="doip.episerve.zib.de", port=3567, use_tls=True)  # the public endpoint is TLS-only
hello = client.hello()
metadata = client.retrieve("Q1748526042817").metadata_blocks
```

## TLS

Place `certs/server.crt` and `certs/server.key` (PEM) to enable TLS automatically. Without them the server speaks plaintext. The compat listener on port 3568 uses the same TLS setting.

## Compat listener and doipy

The compat listener on port 3568 frames each JSON segment with a 4-byte big-endian length and ends a request
with a zero length. `doipy` (via `doip-sdk`) uses the standard DOIP 2.0 text framing instead: each JSON segment
ends with a `#` line, an empty `#` line ends the request, and byte segments start with `@`. A `doipy` request to
port 3568 therefore hangs: the server reads the first four bytes of the JSON as a ~2 GB length and waits. No
client in this repository uses the compat framing.
