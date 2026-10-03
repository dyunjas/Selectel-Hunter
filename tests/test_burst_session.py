import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from app.hunter.account_worker import AccountBurstWorker
from app.selectel.client import SelectelClient
from app.selectel.errors import ErrorType, SelectelError


@pytest.mark.asyncio
@pytest.mark.parametrize("outcome", ["finished", "blocked", "cooldown", "cancelled"])
async def test_cycle_uses_one_client_and_closes_it_on_every_exit(outcome):
    account = SimpleNamespace(
        id=1, telegram_user_id=1, scheduler_status="RUNNING",
        consecutive_network_errors=0, stop_account_after_found=False,
    )
    targets = [SimpleNamespace(subnet_id=str(i), cidr="test", region="ru-3") for i in range(3)]
    repo = SimpleNamespace(
        enabled_targets=AsyncMock(return_value=targets),
        get_account=AsyncMock(return_value=account),
        update_account=AsyncMock(), record_attempt=AsyncMock(),
        scheduler_settings=AsyncMock(return_value=SimpleNamespace(burst_request_delay=0.001)),
    )
    failures = {
        "finished": SelectelError(ErrorType.NO_FREE_IP, "empty"),
        "blocked": SelectelError(ErrorType.PERMISSION_ERROR, "forbidden"),
        "cooldown": SelectelError(ErrorType.RATE_LIMIT, "rate limit", retry_after=10),
        "cancelled": asyncio.CancelledError(),
    }
    client = SimpleNamespace(close=AsyncMock(), create_floating_ip=AsyncMock(side_effect=failures[outcome]))
    factory = AsyncMock(return_value=client)
    worker = AccountBurstWorker(1, repo, factory, AsyncMock())
    if outcome == "cancelled":
        with pytest.raises(asyncio.CancelledError):
            await worker._run_burst(account)
    else:
        expected = {"finished": "finished", "blocked": "stop", "cooldown": "cooldown"}
        assert await worker._run_burst(account) == expected[outcome]
    factory.assert_awaited_once_with(1)
    # Discard any idle UI session before the cycle and close after it.
    assert client.close.await_count == 2
    assert client.create_floating_ip.await_count == (3 if outcome == "finished" else 1)


@pytest.mark.asyncio
@pytest.mark.parametrize("use_proxy", [False, True])
async def test_requests_reuse_connection_within_cycle_and_reconnect_next_cycle(monkeypatch, use_proxy):
    from aiohttp import web
    from app.config.regions import REGIONS

    transports = []

    async def create(request):
        transports.append(request.transport)
        await request.json()
        return web.json_response({"floatingip": {"id": "ip"}})

    server = web.Application()
    server.router.add_post("/floatingips", create)
    runner = web.AppRunner(server)
    await runner.setup()
    site = web.TCPSite(runner, "127.0.0.1", 0)
    await site.start()
    port = site._server.sockets[0].getsockname()[1]
    server_url = f"http://127.0.0.1:{port}"
    monkeypatch.setitem(REGIONS, "ru-3", {
        **REGIONS["ru-3"], "network_api_url": "http://api.test" if use_proxy else server_url,
    })
    client = SelectelClient(
        SimpleNamespace(region="ru-3", network_api_url="unused"), "secret",
        proxy=server_url if use_proxy else None,
    )
    client.token = "token"
    try:
        await client.create_floating_ip("ru-3", "first")
        session = client.session
        await client.create_floating_ip("ru-3", "second")
        assert client.session is session
        assert transports[0] is transports[1]
        assert session.timeout.total == 5
        await client.close()
        assert session.closed
        await client.create_floating_ip("ru-3", "third")
        assert client.session is not session
        assert transports[2] is not transports[0]
    finally:
        await client.close()
        await runner.cleanup()
