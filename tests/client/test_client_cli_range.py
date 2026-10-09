"""CLI --size / --offset for partial component retrieves."""

import json

import pytest

from client_cli import main as cli
from doip_client.client import StrictDOIPClient
from doip_client.messages import ComponentBlock, DoipResponse
from doip_client.protocol import Header

BASE = ["--no-tls", "--action", "retrieve", "--object-id", "Q1", "--component", "data.bin"]


def _resp(metadata_blocks=None, component_blocks=None):
    return DoipResponse(
        header=Header(2, 1, 0, 0, 0, 0),
        metadata_blocks=metadata_blocks or [],
        component_blocks=component_blocks or [],
        workflow_blocks=[],
    )


def _capture(monkeypatch, response):
    calls = []

    def fake(self, oid, *a, **k):
        calls.append(k)
        return response

    monkeypatch.setattr(StrictDOIPClient, "retrieve", fake)
    return calls


def _partial():
    return _resp(
        metadata_blocks=[{"element": "data.bin", "partial": True, "offset": 100, "totalSize": 5000}],
        component_blocks=[ComponentBlock(component_id="data.bin", content=b"0123456789", media_type="application/octet-stream")],
    )


def test_size_and_offset_are_passed_to_the_client(monkeypatch, tmp_path):
    calls = _capture(monkeypatch, _partial())
    out = tmp_path / "part.bin"

    rc = cli.main(BASE + ["--size", "10", "--offset", "100", "--output", str(out)])

    assert rc == 0
    assert out.read_bytes() == b"0123456789"
    assert calls[0]["size"] == 10 and calls[0]["offset"] == 100


def test_plain_retrieve_sends_neither(monkeypatch, tmp_path):
    calls = _capture(monkeypatch, _resp(component_blocks=[ComponentBlock(component_id="data.bin", content=b"x", media_type="a/b")]))

    assert cli.main(BASE + ["--output", str(tmp_path / "f")]) == 0
    assert "size" not in calls[0] and "offset" not in calls[0]


def test_json_envelope_reports_offset_total_and_partial(monkeypatch, tmp_path, capsys):
    _capture(monkeypatch, _partial())
    out = tmp_path / "part.bin"

    rc = cli.main(["--force-json-output"] + BASE + ["--size", "10", "--offset", "100", "--output", str(out)])

    assert rc == 0
    result = json.loads(capsys.readouterr().out)["result"]
    assert result["bytes"] == 10
    assert (result["offset"], result["totalSize"], result["partial"]) == (100, 5000, True)


def test_offset_beyond_end_fails_with_a_clear_message(monkeypatch, tmp_path, capsys):
    _capture(monkeypatch, _resp(metadata_blocks=[{"element": "data.bin", "rangeNotSatisfiable": True, "totalSize": 1024}]))

    rc = cli.main(["--force-json-output"] + BASE + ["--offset", "5000", "--output", str(tmp_path / "f")])

    assert rc == 1
    assert "beyond the end of the component (1024 bytes)" in json.loads(capsys.readouterr().out)["error"]


@pytest.mark.parametrize(
    "extra",
    [["--size", "0"], ["--size", "-3"], ["--offset", "-4", "--size", "4"]],
)
def test_invalid_combinations_are_rejected_before_any_request(monkeypatch, extra):
    calls = _capture(monkeypatch, _resp())

    with pytest.raises(SystemExit) as exc:
        cli.main(BASE + extra)

    assert exc.value.code == 2
    assert calls == []


def test_size_without_a_component_is_rejected(monkeypatch):
    calls = _capture(monkeypatch, _resp())

    with pytest.raises(SystemExit) as exc:
        cli.main(["--no-tls", "--action", "retrieve", "--object-id", "Q1", "--size", "10"])

    assert exc.value.code == 2
    assert calls == []
