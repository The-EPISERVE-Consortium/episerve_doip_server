"""Startup check for a newer ``episerve-doip-cli`` release.

Released binaries are stamped with their git tag at build time (see
``.github/workflows/build-doip-cli-binary.yml``), so the CLI knows its own
version. At runtime that version is compared against the newest tag advertised
by the repository's git ref endpoint, which needs no token and -- unlike the
GitHub Releases API -- is not rate limited.

Every failure mode (no network, timeout, malformed response, unparseable
version) is reported as "no update", so this can never break a CLI run.
"""

from __future__ import annotations

import json
import os
import re
import sys
import time
import urllib.request
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path

from doip_shared.version import __version__

REPO_URL = "https://github.com/The-EPISERVE-Consortium/episerve_doip_server"
HOST = "github.com"  # shown when the update check cannot reach the repository
REF_URL = f"{REPO_URL}/info/refs?service=git-upload-pack"
RELEASES_URL = f"{REPO_URL}/releases/latest"
ENV_OPT_OUT = "EPISERVE_NO_CLI_UPDATE_CHECK"
CACHE_MAX_AGE_SECONDS = 24 * 60 * 60
DEFAULT_TIMEOUT = 2.0

# Tags appear as pkt-line payloads like "<sha> refs/tags/v0.0.6\n".
_TAG_RE = re.compile(r"refs/tags/(\S+)")
# Leading dotted numeric version, tolerating a "v" prefix and any suffix.
_VERSION_RE = re.compile(r"^v?(\d+(?:\.\d+)*)")


@dataclass(frozen=True)
class UpdateInfo:
    """A newer release than the one currently running."""

    current: str
    latest: str
    url: str = RELEASES_URL


def parse_version(text: str) -> tuple[int, ...]:
    """Parse a version string into a comparable tuple.

    Args:
        text: Tag or version such as ``v0.0.6``, ``0.0.6^{}`` or ``0.0.0.dev0``.

    Returns:
        tuple[int, ...]: The leading dotted numeric components, or ``()`` when
        the string does not start with a version number.
    """
    match = _VERSION_RE.match(text.strip())
    if not match:
        return ()
    try:
        return tuple(int(part) for part in match.group(1).split("."))
    except ValueError:
        # CPython refuses int() on absurdly long digit strings; treat as unparseable.
        return ()


def _is_placeholder(version: tuple[int, ...]) -> bool:
    """Return True for versions that should never trigger an update notice.

    Unparsable versions and the all-zero placeholder of source checkouts /
    un-stamped builds would otherwise look older than every release.
    """
    return not version or all(part == 0 for part in version)


def fetch_latest_tag(timeout: float = DEFAULT_TIMEOUT) -> str | None:
    """Return the newest non-prerelease tag advertised by the repository.

    Uses the git ref advertisement endpoint (the one ``git ls-remote`` uses),
    which needs no token and is not subject to the GitHub API rate limit.

    Args:
        timeout: Socket timeout in seconds.

    Returns:
        str | None: The newest tag, or ``None`` when the response lists none.

    Raises:
        Exception: Any network or decoding failure, for the caller to absorb.
        ValueError: If the configured URL is not https (guards against a
            modified REF_URL pointing urlopen at a ``file:`` or custom scheme).
    """
    if not REF_URL.startswith("https://"):
        raise ValueError(f"refusing to fetch update info over a non-https URL: {REF_URL}")
    # REF_URL is a hardcoded https constant, scheme-checked just above.
    request = urllib.request.Request(  # noqa: S310
        REF_URL, headers={"User-Agent": f"episerve-doip-cli/{__version__}"}
    )
    with urllib.request.urlopen(request, timeout=timeout) as response:  # noqa: S310
        body = response.read().decode("utf-8", errors="replace")

    candidates: list[tuple[tuple[int, ...], str]] = []
    for match in _TAG_RE.finditer(body):
        tag = match.group(1).removesuffix("^{}")  # annotated tags are listed twice
        if "rc" in tag.lower():  # prereleases are never offered
            continue
        parsed = parse_version(tag)
        if parsed:
            candidates.append((parsed, tag))
    return max(candidates)[1] if candidates else None


def cache_path() -> Path:
    """Return the per-user path of the update-check cache file."""
    if os.name == "nt":
        base = Path(os.environ.get("LOCALAPPDATA") or Path.home() / "AppData" / "Local")
    else:
        base = Path(os.environ.get("XDG_CACHE_HOME") or Path.home() / ".cache")
    return base / "episerve" / "doip-update-check.json"


def _read_cache(path: Path, max_age: float, now: float) -> tuple[bool, str | None]:
    """Return ``(is_fresh, tag)`` from the cache, treating any error as a miss."""
    try:
        data = json.loads(path.read_text())
        if now - float(data["checked_at"]) <= max_age:
            tag = data.get("tag")
            return True, tag if isinstance(tag, str) else None
    except (OSError, ValueError, TypeError, KeyError):
        return False, None
    return False, None


def _write_cache(path: Path, tag: str | None) -> None:
    """Persist the check result, ignoring an unwritable cache location."""
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps({"checked_at": time.time(), "tag": tag}))
    except OSError:
        pass


def check_for_update(
    current: str = __version__,
    *,
    timeout: float = DEFAULT_TIMEOUT,
    cache_file: Path | None = None,
    max_age: float = CACHE_MAX_AGE_SECONDS,
    force: bool = False,
    now: float | None = None,
) -> UpdateInfo | None:
    """Check whether a newer release than ``current`` is available.

    Args:
        current: Version of the running CLI.
        timeout: Socket timeout for the tag lookup.
        cache_file: Cache location override (defaults to :func:`cache_path`).
        max_age: How long a cached result stays valid, in seconds.
        force: Ignore any cached result and query the network.
        now: Current unix time override, for tests.

    Returns:
        UpdateInfo | None: Details of the newer release, or ``None`` when the
        CLI is current, is an un-stamped build, or the check could not run.
    """
    current_version = parse_version(current)
    if _is_placeholder(current_version):
        return None

    now = time.time() if now is None else now
    path = cache_file if cache_file is not None else cache_path()

    latest_tag: str | None = None
    fresh = False
    if not force:
        fresh, latest_tag = _read_cache(path, max_age, now)
    if not fresh:
        try:
            latest_tag = fetch_latest_tag(timeout=timeout)
        except Exception:
            return None
        _write_cache(path, latest_tag)

    if not latest_tag:
        return None
    latest_version = parse_version(latest_tag)
    if latest_version and latest_version > current_version:
        return UpdateInfo(current=current, latest=latest_tag)
    return None


def check_explicitly(
    current: str = __version__, *, timeout: float = DEFAULT_TIMEOUT
) -> UpdateInfo | None:
    """Check for an update without consulting the cache.

    Unlike :func:`check_for_update`, network failures propagate so that an
    explicitly requested check can report them instead of staying silent.

    Args:
        current: Version of the running CLI.
        timeout: Socket timeout for the tag lookup.

    Returns:
        UpdateInfo | None: Details of the newer release, or ``None`` when the
        CLI is current or is an un-stamped build.
    """
    current_version = parse_version(current)
    if _is_placeholder(current_version):
        return None

    latest_tag = fetch_latest_tag(timeout=timeout)
    _write_cache(cache_path(), latest_tag)
    if not latest_tag:
        return None
    latest_version = parse_version(latest_tag)
    if latest_version and latest_version > current_version:
        return UpdateInfo(current=current, latest=latest_tag)
    return None


def format_notice(info: UpdateInfo) -> str:
    """Render the one-shot update notice shown on stderr."""
    return (
        f"A newer episerve-doip-cli is available: {info.latest} (you have {info.current})\n"
        f"  {info.url}\n"
        f"  Disable this check with --no-cli-update-check or {ENV_OPT_OUT}=1"
    )


def should_check(
    *,
    force_json_output: bool,
    no_cli_update_check: bool,
    env: Mapping[str, str] | None = None,
    is_tty: bool | None = None,
) -> bool:
    """Decide whether the automatic startup check should run.

    Suppressed for machine-readable output, explicit opt-out, CI and
    non-interactive stderr (pipes and redirects), so scripted use is unaffected.
    """
    env = os.environ if env is None else env
    if force_json_output or no_cli_update_check:
        return False
    if env.get(ENV_OPT_OUT) or env.get("CI"):
        return False
    return sys.stderr.isatty() if is_tty is None else is_tty
