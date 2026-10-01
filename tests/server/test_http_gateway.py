"""HTTP gateway: pass the Bearer token to the backend and map access denials."""

import pytest
from fastapi.testclient import TestClient

from doip_client.messages import ComponentBlock, DoipResponse
from doip_client.protocol import Header
from doip_server import http_gateway
from doip_shared.constants import MSG_TYPE_ERROR, MSG_TYPE_RESPONSE, OP_RETRIEVE


def _response(msg_type, metadata=None, components=None):
    return DoipResponse(
        header=Header(2, msg_type, OP_RETRIEVE, 0, 0, 0),
        metadata_blocks=metadata or [],
        component_blocks=components or [],
        workflow_blocks=[],
    )


class FakeClient:
    """Behaves like the backend: restricted unless the token is ``good``."""

    def __init__(self):
        self.tokens = []

    def retrieve(self, object_id, component_id=None, version=None, limit=None, include_sizes=False, token=None):
        self.tokens.append(token)
        if token != "good":
            return _response(MSG_TYPE_ERROR, [{"error": "AccessDeniedError", "message": "access to this object is restricted"}])
        return _response(MSG_TYPE_RESPONSE, components=[ComponentBlock(component_id="data.parquet", content=b"abc", media_type="application/octet-stream")])


@pytest.fixture
def gateway(monkeypatch):
    fake = FakeClient()
    monkeypatch.setattr(http_gateway, "_client", lambda use_tls=None: fake)
    return TestClient(http_gateway.app), fake


URL = "/doip/retrieve/Q1/data.parquet"


def test_no_token_gives_401_with_challenge(gateway):
    client, fake = gateway
    r = client.get(URL)
    assert r.status_code == 401
    assert r.headers["www-authenticate"] == "Bearer"
    assert fake.tokens == [None]


def test_wrong_token_gives_403(gateway):
    client, _ = gateway
    assert client.get(URL, headers={"Authorization": "Bearer nope"}).status_code == 403


def test_valid_token_is_passed_through_and_serves_file(gateway):
    client, fake = gateway
    r = client.get(URL, headers={"Authorization": "Bearer good"})
    assert r.status_code == 200 and r.content == b"abc"
    assert fake.tokens == ["good"]


def test_head_is_protected_too(gateway):
    client, _ = gateway
    assert client.head(URL).status_code == 401
    assert client.head(URL, headers={"Authorization": "Bearer good"}).status_code == 200


def test_non_bearer_authorization_is_ignored(gateway):
    client, fake = gateway
    assert client.get(URL, headers={"Authorization": "Basic Zm9vOmJhcg=="}).status_code == 401
    assert fake.tokens == [None]
