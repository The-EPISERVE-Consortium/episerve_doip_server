"""Optional ``size``/``offset`` on retrieve, and HTTP ``Range`` requests served from partial reads."""

import pytest
from botocore.exceptions import ClientError
from fastapi.testclient import TestClient

from doip_client.client import StrictDOIPClient
from doip_client.messages import ComponentBlock, DoipResponse
from doip_client.protocol import Header
from doip_server import handlers, http_gateway, object_registry, protocol, storage_lakefs
from doip_shared.constants import MSG_TYPE_RESPONSE, OP_RETRIEVE

DATA = bytes(range(256)) * 4  # 1024 bytes


# --- storage -----------------------------------------------------------------

def _storage(monkeypatch, response=None, error=None, head_length=None):
    """Patch the S3 client; returns the dict that records the calls made to it."""
    calls = {}

    async def fake_to_thread(func, *args, **kwargs):
        return func(*args, **kwargs)

    class FakeClient:
        def get_object(self, Bucket=None, Key=None, Range=None):
            calls.update(bucket=Bucket, key=Key, range=Range)
            if error:
                raise error
            return response

        def head_object(self, Bucket=None, Key=None):
            return {"ContentLength": head_length}

    monkeypatch.setattr(storage_lakefs, "_client", lambda: FakeClient())
    monkeypatch.setattr(storage_lakefs.asyncio, "to_thread", fake_to_thread)
    storage_lakefs.configure({"lakefs": {"repos": ["repo-name"], "branch": "main"}})
    return calls


def _body(data):
    class Body:
        def read(self):
            return data

    return Body()


@pytest.mark.asyncio
async def test_range_with_offset_and_size(monkeypatch):
    calls = _storage(monkeypatch, {"Body": _body(DATA[100:110]), "ContentRange": "bytes 100-109/3571405900"})

    result = await storage_lakefs.get_component_range("Q4", "big.parquet", "repo-name", offset=100, size=10)

    assert result == (DATA[100:110], 100, 3571405900)
    assert calls["range"] == "bytes=100-109"
    assert calls["key"] == "main/00/00/04/Q4/components/big.parquet"


@pytest.mark.asyncio
async def test_range_defaults_to_start_of_file(monkeypatch):
    calls = _storage(monkeypatch, {"Body": _body(DATA[:10]), "ContentRange": "bytes 0-9/1024"})
    await storage_lakefs.get_component_range("Q4", "x", "repo-name", size=10)
    assert calls["range"] == "bytes=0-9"


@pytest.mark.asyncio
async def test_range_to_end_of_file(monkeypatch):
    calls = _storage(monkeypatch, {"Body": _body(DATA[1000:]), "ContentRange": "bytes 1000-1023/1024"})
    result = await storage_lakefs.get_component_range("Q4", "x", "repo-name", offset=1000)
    assert calls["range"] == "bytes=1000-"
    assert result == (DATA[1000:], 1000, 1024)


@pytest.mark.asyncio
async def test_range_from_the_end(monkeypatch):
    calls = _storage(monkeypatch, {"Body": _body(DATA[-8:]), "ContentRange": "bytes 1016-1023/1024"})
    result = await storage_lakefs.get_component_range("Q4", "x", "repo-name", offset=-8)
    assert calls["range"] == "bytes=-8"
    assert result == (DATA[-8:], 1016, 1024)


@pytest.mark.asyncio
async def test_total_is_length_when_object_is_smaller_than_range(monkeypatch):
    _storage(monkeypatch, {"Body": _body(b"abc")})  # no ContentRange: whole object returned
    assert await storage_lakefs.get_component_range("Q4", "x", "repo-name", size=100) == (b"abc", 0, 3)


@pytest.mark.asyncio
async def test_offset_beyond_end_reports_total(monkeypatch):
    error = ClientError({"Error": {"Code": "InvalidRange", "Message": "bad range"}}, "GetObject")
    _storage(monkeypatch, error=error, head_length=1024)
    with pytest.raises(storage_lakefs.RangeNotSatisfiableError) as exc:
        await storage_lakefs.get_component_range("Q4", "x", "repo-name", offset=5000, size=10)
    assert exc.value.total == 1024


@pytest.mark.asyncio
async def test_missing_object_is_key_error(monkeypatch):
    error = ClientError({"Error": {"Code": "NoSuchKey", "Message": "nope"}}, "GetObject")
    _storage(monkeypatch, error=error)
    with pytest.raises(KeyError):
        await storage_lakefs.get_component_range("Q4", "x", "repo-name", size=10)


@pytest.mark.asyncio
@pytest.mark.parametrize("kwargs", [{"size": 0}, {"offset": -5, "size": 5}])
async def test_range_rejects_invalid_arguments(kwargs):
    with pytest.raises(ValueError):
        await storage_lakefs.get_component_range("Q4", "x", "repo-name", **kwargs)


# --- handler -----------------------------------------------------------------

class RangeRegistry(object_registry.ObjectRegistry):
    def __init__(self):
        super().__init__()
        self.calls = []

    async def fetch_fdo_object(self, pid):
        return {"kernel": {"fdo:hasComponent": []}}

    async def get_component(self, pid, element, version=None):
        self.calls.append(("full", element))
        return DATA, "application/octet-stream"

    async def get_component_range(self, pid, element, offset=0, size=None, version=None):
        self.calls.append(("range", element, offset, size))
        total = len(DATA)
        if offset >= total:
            raise storage_lakefs.RangeNotSatisfiableError(total)
        start = total + offset if offset < 0 else offset
        end = total if size is None else min(total, start + size)
        return DATA[start:end], "application/octet-stream", start, total


def _request(**meta):
    return protocol.DOIPMessage(
        version=protocol.DOIP_VERSION,
        msg_type=protocol.MSG_TYPE_REQUEST,
        operation=protocol.OP_RETRIEVE,
        flags=0,
        object_id="Q1",
        metadata_blocks=[{"operation": "retrieve", "element": "data.bin", **meta}],
    )


@pytest.mark.asyncio
async def test_handler_without_size_or_offset_returns_whole_component():
    registry = RangeRegistry()
    response = await handlers.handle_retrieve(_request(), registry)

    assert registry.calls == [("full", "data.bin")]
    assert response.component_blocks[0].content == DATA
    assert response.metadata_blocks == []


@pytest.mark.asyncio
async def test_handler_offset_zero_alone_is_the_whole_component():
    registry = RangeRegistry()
    await handlers.handle_retrieve(_request(offset=0), registry)
    assert registry.calls == [("full", "data.bin")]


@pytest.mark.asyncio
async def test_handler_with_size_returns_leading_bytes_and_total():
    registry = RangeRegistry()
    response = await handlers.handle_retrieve(_request(size=16), registry)

    assert registry.calls == [("range", "data.bin", 0, 16)]
    assert response.component_blocks[0].content == DATA[:16]
    assert response.metadata_blocks == [{"element": "data.bin", "partial": True, "offset": 0, "totalSize": len(DATA)}]


@pytest.mark.asyncio
async def test_handler_with_offset_and_size():
    response = await handlers.handle_retrieve(_request(offset=1000, size=10), RangeRegistry())

    assert response.component_blocks[0].content == DATA[1000:1010]
    assert response.metadata_blocks[0]["offset"] == 1000


@pytest.mark.asyncio
async def test_handler_negative_offset_reads_from_the_end():
    response = await handlers.handle_retrieve(_request(offset=-8), RangeRegistry())

    assert response.component_blocks[0].content == DATA[-8:]
    assert response.metadata_blocks[0]["offset"] == len(DATA) - 8


@pytest.mark.asyncio
async def test_handler_size_larger_than_component_is_not_partial():
    response = await handlers.handle_retrieve(_request(size=10_000), RangeRegistry())

    assert response.component_blocks[0].content == DATA
    assert response.metadata_blocks[0]["partial"] is False


@pytest.mark.asyncio
async def test_handler_offset_beyond_end_is_flagged_without_component():
    response = await handlers.handle_retrieve(_request(offset=5000, size=10), RangeRegistry())

    assert response.component_blocks == []
    assert response.metadata_blocks == [{"element": "data.bin", "rangeNotSatisfiable": True, "totalSize": len(DATA)}]


@pytest.mark.asyncio
@pytest.mark.parametrize("meta", [{"size": 0}, {"size": -5}, {"size": "abc"}, {"offset": "abc"}, {"offset": -4, "size": 4}])
async def test_handler_rejects_invalid_arguments(meta):
    with pytest.raises(protocol.ProtocolError):
        await handlers.handle_retrieve(_request(**meta), RangeRegistry())


# --- client ------------------------------------------------------------------

def test_client_sends_size_and_offset_only_when_given(monkeypatch):
    sent = []
    monkeypatch.setattr(StrictDOIPClient, "send_message", lambda self, request: sent.append(request) or None)
    client = StrictDOIPClient("h", 1, use_tls=False)

    client.retrieve("Q1", "f.parquet", size=1024, offset=2048)
    client.retrieve("Q1", "f.parquet")

    assert sent[0].metadata_blocks[0]["size"] == 1024
    assert sent[0].metadata_blocks[0]["offset"] == 2048
    assert "size" not in sent[1].metadata_blocks[0]
    assert "offset" not in sent[1].metadata_blocks[0]


# --- HTTP gateway ------------------------------------------------------------

class RangeAwareClient:
    """Backend stand-in that honours ``size``/``offset`` the way the real server does."""

    def __init__(self):
        self.requests = []
        self.honours_range = True

    def retrieve(self, object_id, component_id=None, version=None, limit=None, include_sizes=False, token=None, exp=None, sig=None, size=None, offset=None):
        self.requests.append({"size": size, "offset": offset, "exp": exp, "sig": sig})
        total = len(DATA)
        if size is None and offset is None or not self.honours_range:
            return self._response(component_id, DATA, [])
        if offset is not None and offset >= total:
            return self._response(None, b"", [{"element": component_id, "rangeNotSatisfiable": True, "totalSize": total}])
        start = total + offset if (offset or 0) < 0 else (offset or 0)
        content = DATA[start:] if size is None else DATA[start:start + size]
        meta = [{"element": component_id, "partial": len(content) < total, "offset": start, "totalSize": total}]
        return self._response(component_id, content, meta)

    @staticmethod
    def _response(component_id, content, meta):
        blocks = [ComponentBlock(component_id=component_id, content=content, media_type="application/octet-stream")] if component_id else []
        return DoipResponse(
            header=Header(2, MSG_TYPE_RESPONSE, OP_RETRIEVE, 0, 0, 0),
            metadata_blocks=meta,
            component_blocks=blocks,
            workflow_blocks=[],
        )


@pytest.fixture
def gateway(monkeypatch):
    fake = RangeAwareClient()
    monkeypatch.setattr(http_gateway, "_client", lambda use_tls=None: fake)
    return TestClient(http_gateway.app), fake


URL = "/doip/retrieve/Q1/data.bin"


def test_gateway_without_size_or_range_serves_everything(gateway):
    client, fake = gateway
    r = client.get(URL)
    assert r.status_code == 200 and r.content == DATA
    assert fake.requests[0]["size"] is None and fake.requests[0]["offset"] is None


def test_gateway_size_parameter_serves_partial_content_with_total(gateway):
    client, fake = gateway
    r = client.get(URL + "?size=100")
    assert r.status_code == 206
    assert r.content == DATA[:100]
    assert r.headers["content-range"] == f"bytes 0-99/{len(DATA)}"
    assert r.headers["content-length"] == "100"
    assert fake.requests[0]["size"] == 100


def test_gateway_size_larger_than_file_serves_it_whole(gateway):
    client, _ = gateway
    r = client.get(URL + "?size=100000")
    assert r.status_code == 200 and r.content == DATA


@pytest.mark.parametrize("bad", ["0", "-1", "abc"])
def test_gateway_rejects_invalid_size(gateway, bad):
    client, _ = gateway
    assert client.get(URL + f"?size={bad}").status_code == 422


def test_gateway_translates_range_header_into_offset_and_size(gateway):
    client, fake = gateway
    r = client.get(URL, headers={"Range": "bytes=100-199"})
    assert r.status_code == 206
    assert r.content == DATA[100:200]
    assert r.headers["content-range"] == f"bytes 100-199/{len(DATA)}"
    assert (fake.requests[0]["offset"], fake.requests[0]["size"]) == (100, 100)


def test_gateway_open_ended_range(gateway):
    client, fake = gateway
    r = client.get(URL, headers={"Range": "bytes=1000-"})
    assert r.status_code == 206 and r.content == DATA[1000:]
    assert r.headers["content-range"] == f"bytes 1000-1023/{len(DATA)}"
    assert (fake.requests[0]["offset"], fake.requests[0]["size"]) == (1000, None)


def test_gateway_suffix_range_reads_the_footer(gateway):
    client, fake = gateway
    r = client.get(URL, headers={"Range": "bytes=-8"})
    assert r.status_code == 206 and r.content == DATA[-8:]
    assert r.headers["content-range"] == f"bytes 1016-1023/{len(DATA)}"
    assert (fake.requests[0]["offset"], fake.requests[0]["size"]) == (-8, None)


def test_gateway_range_past_the_end_gives_416_with_total(gateway):
    client, _ = gateway
    r = client.get(URL, headers={"Range": "bytes=5000-5009"})
    assert r.status_code == 416
    assert r.headers["content-range"] == f"bytes */{len(DATA)}"


def test_gateway_range_covering_the_whole_file_returns_it_as_206(gateway):
    client, _ = gateway
    r = client.get(URL, headers={"Range": "bytes=0-99999"})
    assert r.status_code == 206 and r.content == DATA
    assert r.headers["content-range"] == f"bytes 0-{len(DATA) - 1}/{len(DATA)}"


@pytest.mark.parametrize("header", ["bytes=0-", "bytes=0-1,5-6", "bytes=abc", "items=1-2"])
def test_gateway_unsupported_range_forms_use_the_whole_component(gateway, header):
    client, fake = gateway
    r = client.get(URL, headers={"Range": header})
    assert fake.requests[0]["size"] is None and fake.requests[0]["offset"] is None
    assert r.content == DATA


def test_gateway_range_still_works_when_backend_ignores_it(gateway):
    client, fake = gateway
    fake.honours_range = False
    r = client.get(URL, headers={"Range": "bytes=100-199"})
    assert r.status_code == 206 and r.content == DATA[100:200]
    assert r.headers["content-range"] == f"bytes 100-199/{len(DATA)}"


def test_gateway_size_still_works_when_backend_ignores_it(gateway):
    client, fake = gateway
    fake.honours_range = False
    r = client.get(URL + "?size=10")
    assert r.status_code == 206 and r.content == DATA[:10]
    assert r.headers["content-range"] == f"bytes 0-9/{len(DATA)}"


def test_gateway_signed_link_and_range_are_both_passed_on(gateway):
    client, fake = gateway
    r = client.get(URL + "?exp=4102444800&sig=abc", headers={"Range": "bytes=10-19"})
    assert r.status_code == 206 and r.content == DATA[10:20]
    assert fake.requests[0]["exp"] == "4102444800" and fake.requests[0]["sig"] == "abc"
    assert (fake.requests[0]["offset"], fake.requests[0]["size"]) == (10, 10)


def test_head_reports_full_length_but_asks_backend_for_one_byte(gateway):
    client, fake = gateway
    r = client.head(URL)
    assert r.status_code == 200
    assert r.headers["content-length"] == str(len(DATA))
    assert fake.requests[0]["size"] == 1
