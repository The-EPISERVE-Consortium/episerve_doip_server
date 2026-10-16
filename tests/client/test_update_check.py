"""Tests for the CLI's startup update check."""

import json
import os
import subprocess
import sys
import time
from pathlib import Path

import pytest

from client_cli import main as cli
from client_cli import update_check


def _refs(*tags: str) -> str:
    """Build a minimal git ref advertisement body listing the given tags."""
    lines = ["# service=git-upload-pack\n"]
    for tag in tags:
        lines.append(f"0123456789abcdef0123456789abcdef01234567 refs/tags/{tag}\n")
        lines.append(f"0123456789abcdef0123456789abcdef01234567 refs/tags/{tag}^{{}}\n")
    lines.append("0000")
    return "".join(lines)


@pytest.fixture
def cache_file(tmp_path):
    return tmp_path / "update-check.json"


@pytest.fixture(autouse=True)
def no_real_network(monkeypatch):
    """Fail loudly if a test reaches the network without stubbing urlopen."""

    def _boom(*args, **kwargs):
        raise AssertionError("test attempted a real network call")

    monkeypatch.setattr(update_check.urllib.request, "urlopen", _boom)


def _serve(monkeypatch, body: str) -> None:
    """Stub the HTTP layer with a fixed response body."""

    def _open(*args, **kwargs):
        return _FakeResponse(body)

    monkeypatch.setattr(update_check.urllib.request, "urlopen", _open)


# --- parse_version ---------------------------------------------------------

@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("v0.0.6", (0, 0, 6)),
        ("0.0.6", (0, 0, 6)),
        ("0.0.6^{}", (0, 0, 6)),
        ("v1.10.0", (1, 10, 0)),
        ("0.0.0.dev0", (0, 0, 0)),
        ("not-a-version", ()),
        ("", ()),
    ],
)
def test_parse_version(text, expected):
    assert update_check.parse_version(text) == expected


def test_parse_version_compares_numerically():
    """0.0.10 must be newer than 0.0.9 (string compare would say otherwise)."""
    assert update_check.parse_version("v0.0.10") > update_check.parse_version("v0.0.9")


@pytest.mark.parametrize("text", ["0.0.0.dev0", "v0.0.0", "junk", ""])
def test_placeholder_versions_are_suppressed(text):
    assert update_check._is_placeholder(update_check.parse_version(text)) is True


@pytest.mark.parametrize("text", ["v0.0.1", "v1.0.0"])
def test_real_versions_are_not_placeholders(text):
    assert update_check._is_placeholder(update_check.parse_version(text)) is False


# --- fetch_latest_tag ------------------------------------------------------

def test_fetch_latest_tag_picks_newest(monkeypatch):
    _serve(monkeypatch, _refs("v0.0.1", "v0.0.10", "v0.0.9"))
    assert update_check.fetch_latest_tag() == "v0.0.10"


def test_fetch_latest_tag_ignores_prereleases(monkeypatch):
    """rc tags are never offered, even when numerically newer."""
    _serve(monkeypatch, _refs("v0.0.6", "v0.1.0-rc1", "v0.1.0rc2"))
    assert update_check.fetch_latest_tag() == "v0.0.6"


def test_fetch_latest_tag_strips_annotated_suffix(monkeypatch):
    _serve(monkeypatch, "0123 refs/tags/v0.0.6\n0123 refs/tags/v0.0.6^{}\n0000")
    assert update_check.fetch_latest_tag() == "v0.0.6"


def test_fetch_latest_tag_returns_none_without_tags(monkeypatch):
    _serve(monkeypatch, "# service=git-upload-pack\n0000")
    assert update_check.fetch_latest_tag() is None


class _FakeResponse:
    """Minimal urlopen() context manager returning a fixed body."""

    def __init__(self, body: str):
        self._body = body.encode()

    def read(self) -> bytes:
        return self._body

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False


# --- check_for_update ------------------------------------------------------

def test_check_for_update_reports_newer(monkeypatch, cache_file):
    monkeypatch.setattr(update_check, "fetch_latest_tag", lambda **k: "v0.0.7")
    info = update_check.check_for_update("0.0.6", cache_file=cache_file)
    assert info == update_check.UpdateInfo(current="0.0.6", latest="v0.0.7")


def test_check_for_update_none_when_current(monkeypatch, cache_file):
    monkeypatch.setattr(update_check, "fetch_latest_tag", lambda **k: "v0.0.6")
    assert update_check.check_for_update("0.0.6", cache_file=cache_file) is None


def test_check_for_update_none_when_ahead(monkeypatch, cache_file):
    """A locally newer build is not downgraded."""
    monkeypatch.setattr(update_check, "fetch_latest_tag", lambda **k: "v0.0.6")
    assert update_check.check_for_update("0.0.7", cache_file=cache_file) is None


def test_check_for_update_placeholder_skips_network(monkeypatch, cache_file):
    """The default source-checkout version must never nag, nor hit the network."""
    assert update_check.check_for_update("0.0.0.dev0", cache_file=cache_file) is None


def test_check_for_update_swallows_fetch_errors(monkeypatch, cache_file):
    def _raise(**kwargs):
        raise OSError("no route to host")

    monkeypatch.setattr(update_check, "fetch_latest_tag", _raise)
    assert update_check.check_for_update("0.0.6", cache_file=cache_file) is None


def test_check_for_update_swallows_bad_json(monkeypatch, cache_file):
    monkeypatch.setattr(update_check, "fetch_latest_tag", lambda **k: "v0.0.7")
    cache_file.write_text("{not json")
    assert update_check.check_for_update("0.0.6", cache_file=cache_file) is not None


# --- caching ---------------------------------------------------------------

def test_cache_hit_avoids_network(monkeypatch, cache_file):
    monkeypatch.setattr(update_check, "fetch_latest_tag", lambda **k: "v0.0.7")
    update_check.check_for_update("0.0.6", cache_file=cache_file)

    monkeypatch.setattr(update_check, "fetch_latest_tag", lambda **k: _never())
    info = update_check.check_for_update("0.0.6", cache_file=cache_file)
    assert info is not None
    assert info.latest == "v0.0.7"


def test_stale_cache_triggers_network(monkeypatch, cache_file):
    now = time.time()
    cache_file.write_text(json.dumps({"checked_at": now - 90000, "tag": "v0.0.7"}))
    monkeypatch.setattr(update_check, "fetch_latest_tag", lambda **k: "v0.0.8")
    info = update_check.check_for_update("0.0.6", cache_file=cache_file, now=now)
    assert info is not None
    assert info.latest == "v0.0.8"


def test_force_bypasses_cache(monkeypatch, cache_file):
    cache_file.write_text(json.dumps({"checked_at": time.time(), "tag": "v0.0.7"}))
    monkeypatch.setattr(update_check, "fetch_latest_tag", lambda **k: "v0.0.9")
    info = update_check.check_for_update("0.0.6", cache_file=cache_file, force=True)
    assert info is not None
    assert info.latest == "v0.0.9"


def test_cached_negative_result_is_reused(monkeypatch, cache_file):
    """A cached 'no update' must not re-query on every start."""
    monkeypatch.setattr(update_check, "fetch_latest_tag", lambda **k: "v0.0.6")
    assert update_check.check_for_update("0.0.6", cache_file=cache_file) is None

    monkeypatch.setattr(update_check, "fetch_latest_tag", lambda **k: _never())
    assert update_check.check_for_update("0.0.6", cache_file=cache_file) is None


def _never():
    raise AssertionError("network should not have been called")


def test_unwritable_cache_is_not_fatal(monkeypatch, tmp_path):
    target = tmp_path / "afile"
    target.write_text("x")
    monkeypatch.setattr(update_check, "fetch_latest_tag", lambda **k: "v0.0.7")
    # cache_file points *inside* a regular file, so mkdir/write must fail silently
    info = update_check.check_for_update("0.0.6", cache_file=target / "sub" / "c.json")
    assert info is not None
    assert info.latest == "v0.0.7"


# --- should_check ----------------------------------------------------------

def test_should_check_allows_interactive_run():
    assert update_check.should_check(
        force_json_output=False, no_cli_update_check=False, env={}, is_tty=True
    ) is True


@pytest.mark.parametrize(
    ("kwargs", "env"),
    [
        ({"force_json_output": True}, {}),
        ({"no_cli_update_check": True}, {}),
        ({}, {"CI": "true"}),
        ({}, {update_check.ENV_OPT_OUT: "1"}),
    ],
)
def test_should_check_suppressed(kwargs, env):
    params = {"force_json_output": False, "no_cli_update_check": False}
    params.update(kwargs)
    assert update_check.should_check(env=env, is_tty=True, **params) is False


def test_should_check_suppressed_when_not_a_tty():
    assert update_check.should_check(
        force_json_output=False, no_cli_update_check=False, env={}, is_tty=False
    ) is False


# --- CLI integration -------------------------------------------------------

def test_cli_version_prints_version_to_stdout(monkeypatch, capsys):
    """The version line must stay on stdout so it can be captured."""
    monkeypatch.setattr(cli, "__version__", "0.0.6")
    monkeypatch.setattr(update_check, "check_explicitly", lambda *a, **k: None)
    assert cli.main(["--cli-version"]) == 0
    assert capsys.readouterr().out.strip() == "episerve-doip-cli 0.0.6"


def test_cli_version_reports_update_available(monkeypatch, capsys):
    monkeypatch.setattr(cli, "__version__", "0.0.6")
    monkeypatch.setattr(
        update_check, "check_explicitly",
        lambda *a, **k: update_check.UpdateInfo(current="0.0.6", latest="v0.0.7"),
    )
    rc = cli.main(["--cli-version"])
    captured = capsys.readouterr()
    assert rc == 1
    assert captured.out.strip() == "episerve-doip-cli 0.0.6"
    assert "v0.0.7" in captured.err


def test_cli_version_reports_current(monkeypatch, capsys):
    monkeypatch.setattr(cli, "__version__", "0.0.6")
    monkeypatch.setattr(update_check, "check_explicitly", lambda *a, **k: None)
    assert cli.main(["--cli-version"]) == 0
    assert "latest release" in capsys.readouterr().err


def test_cli_version_reports_lookup_failure(monkeypatch, capsys):
    def _raise(*a, **k):
        raise OSError("dns failure")

    monkeypatch.setattr(cli, "__version__", "0.0.6")
    monkeypatch.setattr(update_check, "check_explicitly", _raise)
    rc = cli.main(["--cli-version"])
    captured = capsys.readouterr()
    assert rc == 2
    # the version is still reported even though the lookup failed
    assert captured.out.strip() == "episerve-doip-cli 0.0.6"
    assert captured.err.strip() == "Cannot reach github.com to check for updates."


def test_lookup_failure_message_is_generic(monkeypatch, capsys):
    """One message for every failure mode, with no raw exception text."""
    for error in (OSError("dns failure"), TimeoutError("timed out"), ValueError("bad json")):
        def _raise(*a, _e=error, **k):
            raise _e

        monkeypatch.setattr(cli, "__version__", "0.0.6")
        monkeypatch.setattr(update_check, "check_explicitly", _raise)
        assert cli.main(["--cli-version"]) == 2
        assert capsys.readouterr().err.strip() == "Cannot reach github.com to check for updates."


def test_cli_version_on_development_build(monkeypatch, capsys):
    monkeypatch.setattr(cli, "__version__", "0.0.0.dev0")
    monkeypatch.setattr(update_check, "check_explicitly", lambda *a, **k: _never())
    assert cli.main(["--cli-version"]) == 0
    assert "development build" in capsys.readouterr().err


def test_automatic_notice_goes_to_stderr_only(monkeypatch, capsys):
    """The notice must never touch stdout, and must not change the exit code."""
    monkeypatch.setattr(cli, "__version__", "0.0.6")
    monkeypatch.setattr(update_check, "should_check", lambda **k: True)
    monkeypatch.setattr(
        update_check, "check_for_update",
        lambda *a, **k: update_check.UpdateInfo(current="0.0.6", latest="v0.0.7"),
    )
    monkeypatch.setattr(cli.StrictDOIPClient, "hello", lambda self: {"status": "ok"})

    rc = cli.main(["--no-tls", "--action", "hello"])

    captured = capsys.readouterr()
    assert rc == 0
    assert "v0.0.7" in captured.err
    assert json.loads(captured.out) == {"status": "ok"}


def test_no_notice_when_should_check_is_false(monkeypatch, capsys):
    monkeypatch.setattr(update_check, "should_check", lambda **k: False)
    monkeypatch.setattr(
        update_check, "check_for_update",
        lambda *a, **k: _never(),
    )
    monkeypatch.setattr(cli.StrictDOIPClient, "hello", lambda self: {"status": "ok"})

    assert cli.main(["--no-tls", "--action", "hello"]) == 0
    assert capsys.readouterr().err == ""


def test_force_json_output_stays_parseable(monkeypatch, capsys):
    """stdout must remain exactly one JSON envelope even when an update exists."""
    monkeypatch.setattr(update_check, "should_check", lambda **k: False)
    monkeypatch.setattr(cli.StrictDOIPClient, "hello", lambda self: {"status": "ok"})

    rc = cli.main(["--no-tls", "--force-json-output", "--action", "hello"])

    captured = capsys.readouterr()
    assert rc == 0
    assert json.loads(captured.out) == {
        "action": "hello",
        "object_id": None,
        "ok": True,
        "result": {"status": "ok"},
    }


def test_network_failure_does_not_break_action(monkeypatch, capsys, cache_file):
    """A dead network during the check must be invisible to the user's action."""
    monkeypatch.setattr(cli, "__version__", "0.0.6")
    monkeypatch.setattr(update_check, "should_check", lambda **k: True)
    monkeypatch.setattr(update_check, "cache_path", lambda: cache_file)

    def _raise(**kwargs):
        raise OSError("network down")

    monkeypatch.setattr(update_check, "fetch_latest_tag", _raise)
    monkeypatch.setattr(cli.StrictDOIPClient, "hello", lambda self: {"status": "ok"})

    rc = cli.main(["--no-tls", "--action", "hello"])

    captured = capsys.readouterr()
    assert rc == 0
    assert "network down" not in captured.err
    assert json.loads(captured.out) == {"status": "ok"}


# --- packaging -------------------------------------------------------------

def test_main_runs_as_a_script_not_only_as_a_package():
    """PyInstaller runs ``client_cli/main.py`` as a script, not as a package.

    In that context ``__package__`` is empty, so any relative import in the
    entrypoint raises ImportError at startup and the binary is dead on arrival.
    This shipped once (v0.0.7), so it is covered explicitly: importing the
    module as ``__main__`` via a file path reproduces the frozen layout.
    """
    entrypoint = Path(cli.__file__).resolve()
    result = subprocess.run(
        [sys.executable, str(entrypoint), "--cli-version"],
        capture_output=True,
        text=True,
        cwd=entrypoint.parent.parent,
        env={**os.environ, "PYTHONPATH": str(entrypoint.parent.parent)},
    )
    assert "ImportError" not in result.stderr, result.stderr
    assert "attempted relative import" not in result.stderr, result.stderr
    assert result.returncode == 0, result.stderr
    assert "episerve-doip-cli" in result.stdout
