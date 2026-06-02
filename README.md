# Episerve DOIP Server

Asyncio-based DOIP 2.0 TCP server that fronts the EPISERVE FDO infrastructure. The server listens on port 3567, uses strict DOIP binary envelopes, streams components from lakeFS (S3-compatible), and integrates with the FDO FastAPI façade and a MediaWiki/Wikibase backend for derived items.

## Documentation
[![Documentation](https://img.shields.io/badge/docs-gh--pages-blue)](https://mardi4nfdi.github.io/mardi_doip_server/)

- Built with MkDocs; see `docs/build_docs.sh` or browse source in `docs/content/`.



## Getting Started
- Requirements: 
  - `pip install -r requirements.txt`.
- Configuration:
  - Either create `config.yaml` and set configuration details.
  - Or use environment variables - they override values from the config file where applicable
    - `LAKEFS_USER` / `LAKEFS_PASSWORD` 
    - `LAKEFS_URL` / `LAKEFS_REPO`
    - `FDO_API`
    - `OLLAMA_API_KEY`

Run the server:
```bash
python -m doip_server.main           # binds 0.0.0.0:3567
```
or, to use a custom FDO server endpoint:
```bash
python -m doip_server.main --fdo-api http://127.0.0.1:8000/fdo/
```


## Getting started with Docker

The Docker version has an additional HTTP gateway to use the DOIP service.  
Example: `/doip/retrieve/{object_id}/{component_id}` (This would stream the given component as file download.)
Or, using curl:
```bash
curl -OJ http://localhost/doip/retrieve/Q123/fulltext
```

Build the image (from repo root):
```bash
docker build -f docker/Dockerfile -t mardi-doip .
```

Run with TLS (default self-signed cert generated during build):
```bash
docker run --rm \
  -p 80:80 -p 3567:3567 -p 3568:3568 \
  -e FDO_API=https://fdo.portal.mardi4nfdi.de/fdo/ \
  -e LAKEFS_URL=<your-lakefs-url> \
  -e LAKEFS_USER=<user> -e LAKEFS_PASSWORD=<pass> -e LAKEFS_REPO=<repo> \
  mardi-doip
```

Example hello via client CLI against the container:
```bash
python -m client_cli.main --host localhost --port 3567 --action hello
```

## Examples

### Run the client CLI:

Retrieve meta-data about an FDO, e.g. a publication with QID Q6190920:

```bash
PYTHONPATH=. python -m client_cli.main --host 127.0.0.1 --no-tls --action retrieve --object-id Q6190920 
```

Retrieve the fulltext pdf from a FDO publication:

```bash
PYTHONPATH=. python -m client_cli.main --host 127.0.0.1 --no-tls --action retrieve --object-id Q6190920 --component fulltext.pdf --output pdf.pdf
```

Update one component on an existing FDO and create a mandatory lakeFS commit:

```bash
PYTHONPATH=. python -m client_cli.main --host 127.0.0.1 --no-tls --action update --object-id Q6190920 --component fulltext.pdf --input pdf.pdf --media-type application/pdf
```


### Use the DOIP client in Python:
```python
from doip_client import StrictDOIPClient

client = StrictDOIPClient(host="127.0.0.1", port=3567, use_tls=False)
hello = client.hello()
metadata = client.retrieve("Q123").metadata_blocks
update = client.update_component("Q123", "fulltext.pdf", b"pdf-bytes", media_type="application/pdf")
```

Component IDs are exact storage names. No file extension is added automatically. If you store `fulltext`, retrieve `fulltext`. If you store `fulltext.pdf`, retrieve `fulltext.pdf`.

## TLS (optional):
- Place `certs/server.crt` and `certs/server.key` (PEM) to enable TLS automatically; otherwise the server speaks plaintext DOIP.
- A compatibility listener runs on port 3568 (same TLS setting) accepting doipy JSON-segmented requests and bridging to the DOIP handlers.


# Latest Development CLI Build


[![Build Status](https://github.com/MaRDI4NFDI/mardi_doip_server/actions/workflows/build-doip-cli-binary.yml/badge.svg)](https://github.com/MaRDI4NFDI/mardi_doip_server/actions/workflows/build-doip-cli-binary.yml)

Download the latest CLI binaries (Windows/macOS/Linux) from the **Artifacts** section of the most recent successful run.
