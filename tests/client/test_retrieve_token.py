"""StrictDOIPClient.retrieve sends a token only when one is passed explicitly."""

from doip_client.client import StrictDOIPClient


def _capture(monkeypatch):
    sent = []
    monkeypatch.setattr(StrictDOIPClient, "send_message", lambda self, request: sent.append(request) or None)
    return sent


def test_token_is_sent_when_given(monkeypatch):
    sent = _capture(monkeypatch)
    StrictDOIPClient("h", 1, use_tls=False).retrieve("Q1", "f.parquet", token="abc")
    assert sent[0].metadata_blocks[0]["token"] == "abc"


def test_no_token_and_no_environment_fallback(monkeypatch):
    # The gateway shares the server's environment; the client must never pick up DOIP_READ_TOKEN itself.
    monkeypatch.setenv("DOIP_READ_TOKEN", "server-secret")
    sent = _capture(monkeypatch)
    StrictDOIPClient("h", 1, use_tls=False).retrieve("Q1", "f.parquet")
    assert "token" not in sent[0].metadata_blocks[0]
