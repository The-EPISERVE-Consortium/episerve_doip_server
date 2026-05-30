# Client CLI

The `client_cli` module provides a minimal command-line interface around the DOIP client.

## Running
```bash
PYTHONPATH=. python -m client_cli.main --object-id Q123 --action retrieve 
```

Options:

- `--host`: DOIP server host (default `doip.staging.mardi4nfdi.org`)
- `--port`: DOIP server port (default `3567`)
- `--no-tls`: Disable TLS wrapping (useful for local plaintext servers)
- `--secure`: Enable TLS certificate/hostname verification
- `--object-id`: Object identifier to retrieve (default `Q123`)
- `--action`: One of `demo`, `hello`, `retrieve`, `update`, `invoke`, `purge` (default `demo`)
- `--component`: Component ID to retrieve (retrieve/demo actions)
- `--input`: File path to upload for `update`
- `--media-type`: Explicit media type for `update`; defaults to `application/octet-stream`
- `--update-token`: Shared secret for `update`; defaults to `DOIP_UPDATE_TOKEN`
- `--workflow`: Workflow name (invoke action, default `equation_extraction`)
- `--params`: Workflow parameters as JSON string (invoke action)
- `--output`: Path or directory to save the first retrieved component (retrieve action)

When saving to a directory, the original filename provided by the server is preserved when present.

### Actions
- `demo`: Runs `hello` then `retrieve`.
- `hello`: Runs only the hello operation.
- `retrieve`: Runs retrieve for the given object (and optional component).
- `update`: Uploads one component to an existing object and creates a lakeFS commit.
- `invoke`: Runs a workflow for the given object with optional params.
- `purge`: Evicts the cached manifest for `--object-id` from the server's in-memory cache.

### Update authorization
- `update` requests must include a shared secret.
- The CLI reads that secret from `--update-token` or `DOIP_UPDATE_TOKEN`.
- On the server, the expected token is currently the configured lakeFS password.

### Example: Download a PDF
```bash
PYTHONPATH=. python -m client_cli.main --action retrieve --object-id Q6190920 --component fulltext.pdf --output .
```

### Example: Download a RO-CRATE
```bash
python -m client_cli.main --host localhost --no-tls --action retrieve --object-id Q6032968 --component rocrate --output crate.zip
```

### Example: Update One Component
```bash
PYTHONPATH=. python -m client_cli.main --host 127.0.0.1 --no-tls --action update --object-id Q6190920 --component fulltext.pdf --input pdf.pdf --media-type application/pdf --update-token "$DOIP_UPDATE_TOKEN"
```

`update` is component-scoped. It updates or adds the specified component and leaves all other components unchanged.
Component IDs are exact storage names. If you upload `fulltext`, retrieve `fulltext`. If you upload `fulltext.pdf`, retrieve `fulltext.pdf`.
