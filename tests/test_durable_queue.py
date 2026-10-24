import asyncio
import json
from datetime import datetime, timedelta
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
import pytest_asyncio
from sqlalchemy import text

from app.config.regions import REGIONS
from app.config.subnets import TARGET_SUBNETS
from app.db.database import Database
from app.db.migration import migrate_queue
from app.db.queue import DurableQueue, LeaseLost
from app.db.repositories import Repository
from app.hunter.durable import DurableBurst
from app.selectel.client import SelectelClient
from app.selectel.errors import ErrorType, SelectelError
from app.services.notifications import NotificationService
from app.services.runtime import InstanceLock


@pytest_asyncio.fixture
async def context(tmp_path):
    path = tmp_path / "test.db"
    db = Database(f"sqlite+aiosqlite:///{path.as_posix()}")
    await db.init()
    repo = Repository(db.sessions)
    account = await repo.add_account(telegram_user_id=42, display_name="test", domain="d", username="u",
        encrypted_password="encrypted", project_id="project", region="ru-3", scheduler_status="RUNNING")
    yield db, repo, account, path
    await db.close()


def target(region):
    return next(t for t in TARGET_SUBNETS if t["region"] == region)


async def claim(repo, account, region="ru-2"):
    await repo.queue.enqueue(account, [target(region)])
    return await repo.queue.claim(account.id)


def resource(job, *, ip=None, **overrides):
    return {"id": "fip", "floating_ip_address": ip or job.cidr.split("/")[0],
        "description": DurableBurst.marker(job), "project_id": "project",
        "floating_network_id": job.network_id, **overrides}


def fake_client():
    return SimpleNamespace(resolve_network=AsyncMock(return_value=REGIONS["ru-2"]["floating_network_id"]),
        create_floating_ip=AsyncMock(), get_floating_ips=AsyncMock(return_value={"floatingips": []}),
        get_floating_ip=AsyncMock(), delete_floating_ip=AsyncMock(), close=AsyncMock())


@pytest.mark.asyncio
@pytest.mark.parametrize("region", ["ru-2", "ru-3"])
async def test_job_snapshots_region_subnet_and_owner(context, region):
    _, repo, account, _ = context
    job = await claim(repo, account, region)
    assert job.region == region and job.subnet_id == target(region)["subnet_id"]
    assert job.telegram_user_id == 42 and job.status == "running"
    assert job.lease_token and job.created_at and job.updated_at


@pytest.mark.asyncio
async def test_regions_are_independent_and_enqueue_is_idempotent(context):
    _, repo, account, _ = context
    await repo.queue.enqueue(account, [target("ru-2"), target("ru-3")])
    await repo.queue.enqueue(account, [target("ru-2")])
    jobs = await repo.queue.list(account_id=account.id)
    assert len(jobs) == 2
    await repo.queue.control(jobs[0].id, 999, "cancelled")
    assert (await repo.queue.get(jobs[0].id)).status == "pending"
    await repo.queue.control(jobs[0].id, 42, "paused")
    assert (await repo.queue.get(jobs[1].id)).status == "pending"


@pytest.mark.asyncio
async def test_multiple_workers_claim_once(context):
    db, repo, account, _ = context
    await repo.queue.enqueue(account, [target("ru-2")])
    other = DurableQueue(db.sessions)
    results = await asyncio.gather(repo.queue.claim(account.id), other.claim(account.id), repo.queue.claim(account.id))
    assert sum(job is not None for job in results) == 1


@pytest.mark.asyncio
async def test_crash_restart_lease_fencing_preserves_intent(context):
    db, repo, account, path = context
    job = await claim(repo, account)
    await repo.queue.begin_operation(job, "network")
    operation_id = job.operation_id
    await db.close()
    restarted = Database(f"sqlite+aiosqlite:///{path.as_posix()}")
    await restarted.init()
    queue = DurableQueue(restarted.sessions)
    try:
        assert await queue.claim(account.id) is None
        await queue.recover(datetime.utcnow() + timedelta(days=7))
        reclaimed = await queue.claim(account.id)
        assert reclaimed.id == job.id and reclaimed.operation_id == operation_id and reclaimed.attempts == 1
        with pytest.raises(LeaseLost):
            await queue.change(job, last_error="stale worker")
        await queue.heartbeat(reclaimed)
    finally:
        await restarted.close()


@pytest.mark.asyncio
async def test_pause_and_cancel_survive_watchdog(context):
    _, repo, account, _ = context
    job = await claim(repo, account)
    await repo.queue.control(job.id, 42, "paused")
    await repo.queue.recover(datetime.utcnow() + timedelta(days=7))
    assert await repo.queue.claim(account.id) is None
    assert (await repo.queue.get(job.id)).status == "paused"
    await repo.queue.control(job.id, 42, "pending")
    reclaimed = await repo.queue.claim(account.id)
    await repo.queue.control(job.id, 42, "cancelled")
    await repo.queue.release(reclaimed)
    assert (await repo.queue.get(job.id)).status == "cancelled"


@pytest.mark.asyncio
async def test_success_is_atomic_and_telegram_failure_retries(context):
    _, repo, account, _ = context
    job = await claim(repo, account)
    client = fake_client()
    async def create(*args, **kwargs):
        return resource(job)
    client.create_floating_ip.side_effect = create
    await DurableBurst(repo, AsyncMock()).attempt(job, account, client)
    saved = await repo.queue.get(job.id)
    assert saved.status == "completed" and saved.found_id == "fip"
    found = await repo.found(42)
    assert len(found) == 1
    outbox = await repo.queue.pending_notifications()
    # A lifecycle notification now accompanies the existing FOUND notification.
    outbox = [item for item in outbox if item.event_type == "FOUND"]
    assert len(outbox) == 1
    bot = SimpleNamespace(send_message=AsyncMock(side_effect=ConnectionError("offline")))
    service = NotificationService(bot, repo)
    payload = json.loads(outbox[0].payload)
    task = await repo.get_task(payload["task_id"])
    with pytest.raises(ConnectionError):
        await service.send(task, payload["ip"], payload["fip_id"], 0, "FOUND", durable=True)
    await repo.queue.notification_result(outbox[0], False, delay=0)
    assert len([item for item in await repo.queue.pending_notifications() if item.event_type == "FOUND"]) == 1
    assert len(await repo.found(42)) == 1
    bot.send_message.side_effect = None
    assert await service.send(task, payload["ip"], payload["fip_id"], 0, "FOUND", durable=True)
    await repo.queue.notification_result(outbox[0], True)
    assert not [item for item in await repo.queue.pending_notifications() if item.event_type == "FOUND"]
    with pytest.raises(LeaseLost):
        await repo.queue.complete(job, resource(job))
    assert len(await repo.found(42)) == 1


@pytest.mark.asyncio
async def test_lost_post_response_reconciles_without_second_allocation(context):
    _, repo, account, _ = context
    job = await claim(repo, account)
    client = fake_client()
    client.create_floating_ip.side_effect = SelectelError(ErrorType.NETWORK_ERROR, "timeout")
    burst = DurableBurst(repo, AsyncMock())
    await burst.attempt(job, account, client)
    saved = await repo.queue.get(job.id)
    assert saved.operation_id and saved.attempts == 1
    await repo.queue.control(job.id, 42, "pending")
    job = await repo.queue.claim(account.id)
    client.get_floating_ips.return_value = {"floatingips": [resource(job)]}
    await burst.attempt(job, account, client)
    client.create_floating_ip.assert_awaited_once()
    assert (await repo.queue.get(job.id)).status == "completed"


@pytest.mark.asyncio
async def test_week_of_ambiguous_results_never_repeats_post(context, monkeypatch):
    import app.db.queue as module
    _, repo, account, _ = context
    simulated = datetime.utcnow()
    monkeypatch.setattr(module, "now", lambda: simulated)
    job = await claim(repo, account)
    client = fake_client()
    client.create_floating_ip.side_effect = SelectelError(ErrorType.SERVER_ERROR, "uncertain", status=503)
    burst = DurableBurst(repo, AsyncMock())
    await burst.attempt(job, account, client)
    for day in range(8):
        simulated += timedelta(days=1)
        job = await repo.queue.claim(account.id)
        assert job is not None
        await burst.attempt(job, account, client)
    client.create_floating_ip.assert_awaited_once()
    assert (await repo.queue.get(job.id)).operation_id


@pytest.mark.asyncio
@pytest.mark.parametrize("kind,status", [(ErrorType.RATE_LIMIT, 429), (ErrorType.PERMISSION_ERROR, 403), (ErrorType.NO_FREE_IP, 409)])
async def test_definite_failure_preserves_queue_and_honors_retry_after(context, kind, status):
    _, repo, account, _ = context
    job = await claim(repo, account)
    client = fake_client()
    client.create_floating_ip.side_effect = SelectelError(kind, "rejected", retry_after=7200, status=status)
    await DurableBurst(repo, AsyncMock()).attempt(job, account, client)
    saved = await repo.queue.get(job.id)
    assert saved.operation_id is None and saved.attempts == 1 and saved.errors == 1
    assert saved.due_at >= datetime.utcnow() + timedelta(seconds=7100)
    assert saved.status == ("failed" if status == 403 else "pending")
    if status == 429:
        assert (await repo.get_account(account.id)).scheduler_status == "RATE_LIMIT_COOLDOWN"


@pytest.mark.asyncio
@pytest.mark.parametrize("foreign", [False, True])
async def test_rejected_resource_deleted_only_after_ownership_check(context, foreign):
    _, repo, account, _ = context
    job = await claim(repo, account)
    client = fake_client()
    async def create(*args, **kwargs):
        return resource(job, ip="192.0.2.1")
    async def get(*args):
        return resource(job, ip="192.0.2.1", project_id="other" if foreign else "project")
    client.create_floating_ip.side_effect = create
    client.get_floating_ip.side_effect = get
    await DurableBurst(repo, AsyncMock()).attempt(job, account, client)
    if foreign:
        client.delete_floating_ip.assert_not_awaited()
        assert (await repo.queue.get(job.id)).status == "failed"
    else:
        client.delete_floating_ip.assert_awaited_once_with("fip", "ru-2")
        assert (await repo.queue.get(job.id)).operation_id is None
    assert not await repo.found(42)


@pytest.mark.asyncio
async def test_migration_backup_wal_and_repeated_start(context):
    db, repo, account, path = context
    async with db.session() as s:
        await s.execute(text("PRAGMA user_version=0"))
        await s.commit()
    await db.init()
    assert list(path.parent.glob("*.bak"))
    async with db.session() as s:
        assert await s.scalar(text("PRAGMA journal_mode")) == "wal"
        assert await s.scalar(text("PRAGMA foreign_keys")) == 1
    await migrate_queue(repo)
    before = [(j.id, j.region) for j in await repo.queue.list()]
    assert before and all(region != "ru-2" for _, region in before)
    await migrate_queue(repo)
    await db.init()
    assert before == [(j.id, j.region) for j in await repo.queue.list()]
    assert (await repo.get_account(account.id)).telegram_user_id == 42


def test_duplicate_process_lock(tmp_path):
    path = tmp_path / "worker.lock"
    with InstanceLock(path):
        with pytest.raises(RuntimeError):
            with InstanceLock(path):
                pytest.fail("second process lock acquired")
    with InstanceLock(path):
        pass


@pytest.mark.asyncio
async def test_moscow_fixed_network_no_pool_discovery():
    client = SelectelClient(SimpleNamespace(region="ru-3", network_api_url="unused"), "secret")
    client._request = AsyncMock(return_value={"floatingip": {"id": "fip"}})
    client.get_floatingip_pools = AsyncMock(side_effect=AssertionError("must not discover network"))
    network = "8cc4ee6a-8dc9-4865-8877-ef5b5cdef020"
    assert await client.resolve_network("ru-2", target("ru-2")["subnet_id"]) == network
    for subnet in [t for t in TARGET_SUBNETS if t["region"] == "ru-2"]:
        await client.create_floating_ip("ru-2", subnet["subnet_id"])
        args = client._request.call_args
        assert args.kwargs["region"] == "ru-2"
        assert args.kwargs["json"]["floatingip"] == {"floating_network_id": network, "subnet_id": subnet["subnet_id"]}
    client.get_floatingip_pools.assert_not_awaited()


@pytest.mark.asyncio
async def test_subnet_refresh_keeps_fixed_network_and_other_regions(context):
    _, repo, account, _ = context
    fixed = REGIONS["ru-2"]["floating_network_id"]
    await repo.refresh_pools("ru-2", [{"subnet_id": "new-subnet", "cidr": "192.0.2.0/24", "network_id": "ignored-network"}])
    targets = await repo.all_targets()
    assert any(t.subnet_id == "new-subnet" and t.region == "ru-2" for t in targets)
    assert any(t.subnet_id == target("ru-3")["subnet_id"] for t in targets)
    assert REGIONS["ru-2"]["floating_network_id"] == fixed


@pytest.mark.asyncio
async def test_old_account_schema_migrates_without_losing_settings(tmp_path):
    import sqlite3
    path = tmp_path / "legacy.db"
    with sqlite3.connect(path) as conn:
        conn.execute("""CREATE TABLE accounts (
            id INTEGER PRIMARY KEY, telegram_user_id INTEGER, display_name TEXT,
            domain TEXT, username TEXT, encrypted_password TEXT, project_id TEXT,
            project_name TEXT, region TEXT, auth_url TEXT, network_api_url TEXT,
            enabled BOOLEAN, auto_start BOOLEAN, created_at DATETIME, updated_at DATETIME)""")
        conn.execute("""INSERT INTO accounts VALUES (1,42,'legacy','domain','user','encrypted','project',
            'project','ru-3','auth','original-endpoint',1,0,CURRENT_TIMESTAMP,CURRENT_TIMESTAMP)""")
    db = Database(f"sqlite+aiosqlite:///{path.as_posix()}")
    try:
        await db.init()
        repo = Repository(db.sessions)
        await migrate_queue(repo)
        account = await repo.get_account(1)
        assert account.network_api_url == "original-endpoint" and account.telegram_user_id == 42
        assert account.encrypted_password == "encrypted" and account.region == "ru-3"
        assert len(list(tmp_path.glob("*.bak"))) == 1
        await db.init()
        await migrate_queue(repo)
        assert len(list(tmp_path.glob("*.bak"))) == 1
    finally:
        await db.close()


@pytest.mark.asyncio
async def test_durable_burst_integrates_with_existing_account_scheduler(context):
    _, repo, account, _ = context
    await repo.queue.enqueue(account, [target("ru-2"), target("ru-3")])
    client = fake_client()
    client.create_floating_ip.side_effect = SelectelError(ErrorType.NO_FREE_IP, "empty", status=409)
    await repo.update_scheduler_settings(burst_request_delay=0.001)
    from app.hunter.account_worker import AccountBurstWorker
    worker = AccountBurstWorker(account.id, repo, AsyncMock(return_value=client), AsyncMock())
    assert await worker.run_once() == "finished"
    assert client.create_floating_ip.await_count == 2
    assert {call.args[0] for call in client.create_floating_ip.call_args_list} == {"ru-2", "ru-3"}
    assert all(j.attempts == 1 and j.status == "pending" for j in await repo.queue.list())
    client.close.assert_awaited_once()


@pytest.mark.asyncio
@pytest.mark.parametrize("kind,status", [(ErrorType.NO_FREE_IP, 409), (ErrorType.RATE_LIMIT, 429), (ErrorType.SERVER_ERROR, 503)])
async def test_durable_request_logs_context_and_result_without_secrets(context, caplog, kind, status):
    import logging
    _, repo, account, _ = context
    job = await claim(repo, account)
    client = fake_client()
    client.create_floating_ip.side_effect = SelectelError(kind, "password=must-not-be-logged token=private", retry_after=120, status=status)
    with caplog.at_level(logging.INFO, logger="app.hunter.durable"):
        await DurableBurst(repo, AsyncMock()).attempt(job, account, client)
    assert f"job={job.id}" in caplog.text and "region=ru-2" in caplog.text
    assert f"subnet={job.cidr}" in caplog.text and "attempt=1" in caplog.text
    assert "result=START" in caplog.text and f"result={kind.value}" in caplog.text
    assert f"http={status}" in caplog.text and "retry_seconds=" in caplog.text
    assert "must-not-be-logged" not in caplog.text and "token=private" not in caplog.text


@pytest.mark.asyncio
async def test_reconciliation_logging_does_not_allocate_again(context, caplog):
    import logging
    _, repo, account, _ = context
    job = await claim(repo, account)
    await repo.queue.begin_operation(job, "network")
    client = fake_client()
    with caplog.at_level(logging.INFO, logger="app.hunter.durable"):
        await DurableBurst(repo, AsyncMock()).attempt(job, account, client)
    assert "action=reconcile" in caplog.text and "result=UNRESOLVED" in caplog.text
    assert "retry_seconds=300" in caplog.text
    client.create_floating_ip.assert_not_awaited()
