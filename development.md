# Development

## Local environment
- Create a venv: `python -m venv .venv && source .venv/bin/activate`.
- Install deps: `pip install -r requirements.txt` (add `-e .` during active dev).

## Running tests & quality checks
- Unit tests: `pytest --maxfail=1 --disable-warnings -q`
- Style (if available): `ruff check src tests` and `black src tests`

## Docs workflow
- Edit pages in `docs/content/` and update navigation in `docs/mkdocs.yml`.
- Preview locally: `cd docs && mkdocs serve --config-file mkdocs.yml`
- Build static site (used for GitHub Pages): `cd docs && ./build_docs.sh`

## Project layout
The repo keeps runtime code under `doip_server/`, `doip_client/`, and `client_cli/`, with supporting scripts in `scripts/` and tests in `tests/`. See `project_structure.md` for a directory tour.
