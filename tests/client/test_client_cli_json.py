"""Tests for the CLI's --force-json-output machine-readable mode."""

import json

import pytest

from client_cli import main as cli
from doip_client.client import StrictDOIPClient
from doip_client.messages import DoipResponse
from doip_client.protocol import Header
from doip_shared.constants import MSG_TYPE_ERROR


def _resp(metadata_blocks=None, component_blocks=None):
    return DoipResponse(
        header=Header(2, 1, 0, 0, 0, 0),
        metadata_blocks=metadata_blocks or [],
        component_blocks=component_blocks or [],
        workflow_blocks=[],
    )


def test_retrieve_metadata_emits_only_envelope(monkeypatch, capsys):
    """retrieve in JSON mode prints one envelope and no 'Metadata:' prefix."""
    monkeypatch.setattr(StrictDOIPClient, "retrieve",
                        lambda self, oid, *a, **k: _resp(metadata_blocks=[{"id": oid}]))

    rc = cli.main(["--no-tls", "--force-json-output", "--action", "retrieve",
                   "--object-id", "Q123"])

    assert rc == 0
    out = capsys.readouterr().out
    assert json.loads(out) == {
        "action": "retrieve",
        "object_id": "Q123",
        "ok": True,
        "result": {"metadata_blocks": [{"id": "Q123"}]},
    }


def test_hello_envelope_has_null_object_id(monkeypatch, capsys):
    monkeypatch.setattr(StrictDOIPClient, "hello", lambda self: {"protocol": "DOIP/2.0"})

    rc = cli.main(["--no-tls", "--force-json-output", "--action", "hello"])

    assert rc == 0
    assert json.loads(capsys.readouterr().out) == {
        "action": "hello",
        "object_id": None,
        "ok": True,
        "result": {"protocol": "DOIP/2.0"},
    }


def test_versions_result_wraps_versions_list(monkeypatch, capsys):
    monkeypatch.setattr(StrictDOIPClient, "retrieve",
                        lambda self, oid, *a, **k: _resp(metadata_blocks=[{"versions": [{"commit": "abc"}]}]))

    rc = cli.main(["--no-tls", "--force-json-output", "--action", "versions",
                   "--object-id", "Q123"])

    assert rc == 0
    assert json.loads(capsys.readouterr().out) == {
        "action": "versions",
        "object_id": "Q123",
        "ok": True,
        "result": {"versions": [{"commit": "abc"}]},
    }


def test_client_error_becomes_failure_envelope(monkeypatch, capsys):
    """A client exception is reported in the envelope, not just on stderr, rc=1."""
    def boom(self):
        raise ConnectionError("connection refused")
    monkeypatch.setattr(StrictDOIPClient, "hello", boom)

    rc = cli.main(["--no-tls", "--force-json-output", "--action", "hello"])

    assert rc == 1
    payload = json.loads(capsys.readouterr().out)
    assert payload["action"] == "hello"
    assert payload["ok"] is False
    assert "connection refused" in payload["error"]


def test_component_retrieve_without_output_is_failure_envelope(monkeypatch, capsys):
    """In JSON mode a component fetch requires --output; missing it is a clean error."""
    called = False

    def _retrieve(self, oid, *a, **k):
        nonlocal called
        called = True
        return _resp()
    monkeypatch.setattr(StrictDOIPClient, "retrieve", _retrieve)

    rc = cli.main(["--no-tls", "--force-json-output", "--action", "retrieve",
                   "--object-id", "Q123", "--component", "components/output/x.tsv"])

    assert rc == 1
    payload = json.loads(capsys.readouterr().out)
    assert payload["ok"] is False
    assert "--output" in payload["error"]
    assert called is False


def test_component_retrieve_with_output_reports_saved_file(monkeypatch, capsys, tmp_path):
    from doip_client.messages import ComponentBlock

    block = ComponentBlock(component_id="components/output/x.tsv",
                           content=b"col\n1\n", media_type="text/tab-separated-values")
    monkeypatch.setattr(StrictDOIPClient, "retrieve",
                        lambda self, oid, *a, **k: _resp(component_blocks=[block]))

    dest = tmp_path / "x.tsv"
    rc = cli.main(["--no-tls", "--force-json-output", "--action", "retrieve",
                   "--object-id", "Q123", "--component", "components/output/x.tsv",
                   "--output", str(dest)])

    assert rc == 0
    assert dest.read_bytes() == b"col\n1\n"
    assert json.loads(capsys.readouterr().out) == {
        "action": "retrieve",
        "object_id": "Q123",
        "ok": True,
        "result": {
            "saved_to": str(dest),
            "media_type": "text/tab-separated-values",
            "bytes": 6,
        },
    }


def test_no_flag_keeps_legacy_metadata_prefix(monkeypatch, capsys):
    """Without the flag, output is unchanged (still has the 'Metadata:' line)."""
    monkeypatch.setattr(StrictDOIPClient, "retrieve",
                        lambda self, oid, *a, **k: _resp(metadata_blocks=[{"id": oid}]))

    rc = cli.main(["--no-tls", "--action", "retrieve", "--object-id", "Q123"])

    assert rc == 0
    assert "Metadata:" in capsys.readouterr().out


def _error_resp(kind="ProtocolError", message="No FDO metadata file found for Q123 in any configured repo"):
    return DoipResponse(
        header=Header(2, MSG_TYPE_ERROR, 0, 0, 0, 0),
        metadata_blocks=[{"error": kind, "message": message}],
        component_blocks=[],
        workflow_blocks=[],
    )


@pytest.mark.parametrize("action", ["retrieve", "versions"])
def test_server_error_becomes_failure_envelope(monkeypatch, capsys, action):
    """An error message from the server (an unknown object) is reported as ok:false with exit code 1."""
    monkeypatch.setattr(StrictDOIPClient, "retrieve", lambda self, oid, *a, **k: _error_resp())

    rc = cli.main(["--no-tls", "--force-json-output", "--action", action, "--object-id", "Q123"])

    assert rc == 1
    out = json.loads(capsys.readouterr().out)
    assert out["ok"] is False and out["object_id"] == "Q123"
    assert out["error"].startswith("ProtocolError: No FDO metadata file found for Q123")


def test_server_error_on_a_component_retrieve_is_a_failure(monkeypatch, capsys, tmp_path):
    monkeypatch.setattr(StrictDOIPClient, "retrieve", lambda self, oid, *a, **k: _error_resp())

    rc = cli.main(["--no-tls", "--force-json-output", "--action", "retrieve", "--object-id", "Q123",
                   "--component", "x.parquet", "--output", str(tmp_path / "x.parquet")])

    assert rc == 1 and "No FDO metadata file" in json.loads(capsys.readouterr().out)["error"]
    assert not (tmp_path / "x.parquet").exists()


def test_server_error_is_a_failure_without_json_mode_too(monkeypatch, capsys):
    monkeypatch.setattr(StrictDOIPClient, "retrieve", lambda self, oid, *a, **k: _error_resp())

    rc = cli.main(["--no-tls", "--action", "retrieve", "--object-id", "Q123"])

    captured = capsys.readouterr()
    assert rc == 1 and "No FDO metadata file" in captured.err and "Metadata:" not in captured.out


def test_invoke_and_update_report_server_errors(monkeypatch, capsys, tmp_path):
    monkeypatch.setattr(StrictDOIPClient, "invoke", lambda self, oid, *a, **k: _error_resp("WorkflowError", "no such workflow"))
    rc = cli.main(["--no-tls", "--force-json-output", "--action", "invoke", "--object-id", "Q123", "--workflow", "x"])
    assert rc == 1 and json.loads(capsys.readouterr().out)["error"] == "WorkflowError: no such workflow"

    source = tmp_path / "in.txt"
    source.write_text("x")
    monkeypatch.setattr(StrictDOIPClient, "update_component", lambda self, *a, **k: _error_resp("ProtocolError", "bad"))
    rc = cli.main(["--no-tls", "--force-json-output", "--action", "update", "--object-id", "Q123",
                   "--component", "c", "--input", str(source), "--update-token", "t"])
    assert rc == 1 and json.loads(capsys.readouterr().out)["error"] == "ProtocolError: bad"
