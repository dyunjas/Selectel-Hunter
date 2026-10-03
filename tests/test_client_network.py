import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock

import aiohttp
import pytest
from aiohttp_socks import ProxyConnectionError, ProxyTimeoutError

from app.selectel.client import SelectelClient
from app.selectel.errors import ErrorType, SelectelError


class Response:
    def __init__(self, status=200, payload=None, headers=None):
        self.status = status
        self.payload = {} if payload is None else payload
        self.headers = headers or {}

    async def json(self, **kwargs):
        if isinstance(self.payload, Exception):
            raise self.payload
        return self.payload

    async def __aenter__(self):
        return self

    async def __aexit__(self, *args):
        pass


class Session:
    def __init__(self, outcome):
        self.outcome = outcome
        self.closed = False
        self.calls = []

    def request(self, *args, **kwargs):
        assert not self.closed
        self.calls.append((args, kwargs))
        if isinstance(self.outcome, Exception):
            raise self.outcome
        return self.outcome

    def post(self, *args, **kwargs):
        return self.request("POST", *args, **kwargs)

    async def close(self):
        self.closed = True


def client_with_sessions(monkeypatch, outcomes):
    client = SelectelClient(SimpleNamespace(region="ru-9", network_api_url="unused"), "secret", proxy="http://localhost:8080")
    client.token = "cached-token"
    sessions = [Session(outcome) for outcome in outcomes]
    pending = iter(sessions)

    async def open_session():
        if client.session is None:
            client.session = next(pending)

    monkeypatch.setattr(client, "open", open_session)
    monkeypatch.setattr(client, "_network_retry_delay", AsyncMock())
    return client, sessions


@pytest.mark.asyncio
@pytest.mark.parametrize("failure", [ProxyConnectionError("proxy offline"), ProxyTimeoutError("proxy timeout"), aiohttp.ConnectionTimeoutError("connect timeout")])
async def test_create_reconnects_after_proxy_setup_failure(monkeypatch, failure):
    client, sessions = client_with_sessions(monkeypatch, [failure, failure, Response(payload={"floatingip": {"id": "found"}})])
    assert await client.create_floating_ip("ru-9", "subnet") == {"id": "found"}
    assert all(s.closed for s in sessions[:2])
    assert len(sessions[2].calls) == 1
    assert sessions[2].calls[0][1]["headers"]["X-Auth-Token"] == "cached-token"
    assert client._network_retry_delay.await_count == 2


@pytest.mark.asyncio
@pytest.mark.parametrize("failure", [aiohttp.ServerDisconnectedError(), asyncio.TimeoutError(), aiohttp.ClientPayloadError("truncated")])
async def test_create_does_not_repeat_after_possible_allocation(monkeypatch, failure):
    client, sessions = client_with_sessions(monkeypatch, [Response(payload=failure)])
    with pytest.raises(SelectelError) as error:
        await client.create_floating_ip("ru-9", "subnet")
    assert error.value.kind == ErrorType.NETWORK_ERROR
    assert type(failure).__name__ in str(error.value)
    assert "region=ru-9" in str(error.value)
    assert len(sessions[0].calls) == 1
    client._network_retry_delay.assert_not_awaited()


@pytest.mark.asyncio
async def test_get_retries_disconnect_with_new_session(monkeypatch):
    client, sessions = client_with_sessions(monkeypatch, [aiohttp.ServerDisconnectedError(), Response(payload={"floatingips": []})])
    assert await client.get_floating_ips() == {"floatingips": []}
    assert sessions[0].closed


@pytest.mark.asyncio
async def test_exhausted_retries_remain_network_error(monkeypatch):
    client, sessions = client_with_sessions(monkeypatch, [ProxyConnectionError("offline")] * 3)
    with pytest.raises(SelectelError) as error:
        await client.get_floating_ips()
    assert error.value.kind == ErrorType.NETWORK_ERROR
    assert all(s.closed for s in sessions)
    assert client.session is None


@pytest.mark.asyncio
async def test_authentication_reconnects(monkeypatch):
    client, sessions = client_with_sessions(monkeypatch, [ProxyConnectionError("offline"), Response(headers={"X-Subject-Token": "new-token"})])
    client.account = SimpleNamespace(auth_url="https://auth.test", username="user", domain="domain", project_id="project")
    assert await client.authenticate() == "new-token"
    assert sessions[0].closed


@pytest.mark.asyncio
async def test_401_refreshes_token_once(monkeypatch):
    client, sessions = client_with_sessions(monkeypatch, [Response(status=401)])

    async def relogin():
        client.token = "fresh-token"
        sessions[0].outcome = Response(payload={"floatingip": {"id": "found"}})

    monkeypatch.setattr(client, "relogin", AsyncMock(side_effect=relogin))
    assert await client.create_floating_ip("ru-9", "subnet") == {"id": "found"}
    assert sessions[0].calls[1][1]["headers"]["X-Auth-Token"] == "fresh-token"
    client.relogin.assert_awaited_once()


@pytest.mark.asyncio
async def test_no_free_ip_is_not_retried(monkeypatch):
    client, sessions = client_with_sessions(monkeypatch, [Response(status=409)])
    with pytest.raises(SelectelError) as error:
        await client.create_floating_ip("ru-9", "subnet")
    assert error.value.kind == ErrorType.NO_FREE_IP
    assert len(sessions[0].calls) == 1


@pytest.mark.asyncio
async def test_delete_accepts_empty_204(monkeypatch):
    client, _ = client_with_sessions(monkeypatch, [Response(status=204, payload=ValueError("empty body"))])
    assert await client.delete_floating_ip("fip") == {}


@pytest.mark.asyncio
@pytest.mark.parametrize("proxy", ["http://localhost:8080", "socks5://localhost:1080", None])
async def test_connector_configuration(proxy):
    client = SelectelClient(SimpleNamespace(region="ru-9", network_api_url="unused"), "secret", proxy=proxy)
    try:
        await client.open()
        assert client.session.connector.force_close is False
    finally:
        await client.close()


@pytest.mark.asyncio
async def test_ui_check_waits_for_hunter_request(monkeypatch):
    client, _ = client_with_sessions(monkeypatch, [])
    entered = asyncio.Event()
    release = asyncio.Event()
    calls = []

    async def request(method, path, region=None, **kwargs):
        calls.append(path)
        if len(calls) == 1:
            entered.set()
            await release.wait()
        return {}

    monkeypatch.setattr(client, "_request_unlocked", request)
    first = asyncio.create_task(client.get_floating_ips())
    await entered.wait()
    second = asyncio.create_task(client.validate_account())
    await asyncio.sleep(0)
    assert calls == ["floatingips"]
    release.set()
    await asyncio.gather(first, second)
    assert calls == ["floatingips", "floatingip_pools"]


@pytest.mark.asyncio
async def test_cancellation_is_not_retried(monkeypatch):
    client, _ = client_with_sessions(monkeypatch, [Response(payload=asyncio.CancelledError())])
    # CancelledError is a BaseException, so emulate cancellation at the await.
    monkeypatch.setattr(Response, "json", AsyncMock(side_effect=asyncio.CancelledError()))
    with pytest.raises(asyncio.CancelledError):
        await client.get_floating_ips()
    client._network_retry_delay.assert_not_awaited()
