# Project Layout

- `doip_server/`: Async DOIP 2.0 server, compatibility listener, and workflow plumbing.
- `doip_client/`: Strict DOIP client helpers for Python callers.
- `client_cli/`: Command-line entry points that wrap the client for demos and smoke tests.
- `docs/`: MkDocs sources (`content/`), theme config, and `build_docs.sh` helper.
- `docker/`: Container build context and entrypoint used by `docker/Dockerfile`.
- `config/`: Sample configuration and environment references.
- `scripts/`: Helper scripts to run server/client locally.
- `tests/`: Pytest suites mirroring the runtime layout.
