import asyncio
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

import pytest

from app.hunter.account_manager import BurstSchedulerManager


def utcnow():
    return datetime.now(timezone.utc).replace(tzinfo=None)


def make_account(account_id, *, user_id=1, status="RUNNING", next_cycle_at=None):
    return SimpleNamespace(
        id=account_id,
        telegram_user_id=user_id,
        display_name=f"account-{account_id}",
        enabled=True,
        auto_start=True,
        scheduler_status=status,
        scheduler_position=account_id,
        next_cycle_at=next_cycle_at,
        cooldown_until=None,
        last_cycle_started_at=None,
        last_cycle_finished_at=None,
    )


class FakeRepo:
    def __init__(self, accounts, cooldown=1):
        self.rows = {account.id: account for account in accounts}
        self.settings = SimpleNamespace(burst_cooldown=cooldown)

    async def all_accounts(self):
        return list(self.rows.values())

    async def accounts(self, user_id):
        return [account for account in self.rows.values() if account.telegram_user_id == user_id]

    async def get_account(self, account_id):
        return self.rows.get(account_id)

    async def update_account(self, account_id, **values):
        account = self.rows[account_id]
        for key, value in values.items():
            setattr(account, key, value)

    async def apply_schedule(self, entries):
        for account_id, values in entries:
            await self.update_account(account_id, **values)

    async def scheduler_settings(self):
        return self.settings


class SimulatedBurst:
    def __init__(self, account_id, repo, events, durations, outcomes):
        self.account_id = account_id
        self.repo = repo
        self.events = events
        self.durations = durations
        self.outcomes = outcomes

    async def run_once(self):
        account = await self.repo.get_account(self.account_id)
        started = asyncio.get_running_loop().time()
        self.events.append(("start", self.account_id, started))
        await asyncio.sleep(self.durations.get(self.account_id, 0.005))
        finished = asyncio.get_running_loop().time()
        self.events.append(("finish", self.account_id, finished))
        outcome = self.outcomes.get(self.account_id, "finished")
        if outcome == "429":
            account.scheduler_status = "RATE_LIMIT_COOLDOWN"
            account.cooldown_until = utcnow() + timedelta(seconds=0.5)
            account.next_cycle_at = account.cooldown_until
            return "cooldown"
        if outcome == "403":
            account.scheduler_status = "BLOCKED"
            account.next_cycle_at = None
            return "stop"
        return "finished"


def build_manager(repo, events, durations=None, outcomes=None):
    durations = durations or {}
    outcomes = outcomes or {}
    return BurstSchedulerManager(
        repo,
        client_factory=None,
        notify=None,
        default_cooldown=1,
        worker_factory=lambda account_id, repo, *_args: SimulatedBurst(
            account_id, repo, events, durations, outcomes
        ),
    )


@pytest.mark.asyncio
async def test_twenty_accounts_use_one_non_overlapping_global_executor():
    accounts = [make_account(index) for index in range(1, 21)]
    repo = FakeRepo(accounts)
    events = []
    durations = {index: 0.001 + index * 0.0002 for index in range(1, 21)}
    manager = build_manager(repo, events, durations)
    try:
        started, failed = await manager.start_all_accounts(1)
        assert started == 20
        assert failed == []
        await asyncio.wait_for(_wait_for_events(events, 20), timeout=5)
        starts = [event for event in events if event[0] == "start"]
        finishes = [event for event in events if event[0] == "finish"]
        assert len(starts) == len(finishes) == 20
        for previous, current in zip(finishes, starts[1:]):
            assert current[2] >= previous[2]
    finally:
        await manager.shutdown()


async def _wait_for_events(events, count):
    while len([event for event in events if event[0] == "finish"]) < count:
        await asyncio.sleep(0.001)


@pytest.mark.asyncio
async def test_long_burst_delays_next_account_and_respects_cooldown():
    accounts = [make_account(1), make_account(2)]
    repo = FakeRepo(accounts)
    events = []
    manager = build_manager(repo, events, {1: 0.04, 2: 0.001})
    try:
        await manager.start_all_accounts(1)
        await asyncio.wait_for(_wait_for_events(events, 2), timeout=3)
        first_finish = next(event[2] for event in events if event[:2] == ("finish", 1))
        second_start = next(event[2] for event in events if event[:2] == ("start", 2))
        assert second_start >= first_finish
        assert repo.rows[1].next_cycle_at >= repo.rows[1].last_cycle_finished_at + timedelta(seconds=1)
    finally:
        await manager.shutdown()


@pytest.mark.asyncio
async def test_rate_limit_stops_current_burst_and_persists_retry_after():
    account = make_account(1)
    repo = FakeRepo([account])
    manager = build_manager(repo, [], outcomes={1: "429"})
    await manager.start_all_accounts(1)
    await asyncio.sleep(0.02)
    try:
        assert account.scheduler_status == "RATE_LIMIT_COOLDOWN"
        assert account.cooldown_until is not None
        assert account.next_cycle_at == account.cooldown_until
    finally:
        await manager.shutdown()


@pytest.mark.asyncio
async def test_permission_error_blocks_only_corresponding_account():
    accounts = [make_account(1), make_account(2)]
    repo = FakeRepo(accounts)
    events = []
    manager = build_manager(repo, events, outcomes={1: "403"})
    try:
        await manager.start_all_accounts(1)
        await asyncio.wait_for(_wait_for_events(events, 2), timeout=3)
        assert accounts[0].scheduler_status == "BLOCKED"
        assert accounts[1].scheduler_status == "RUNNING"
    finally:
        await manager.shutdown()


@pytest.mark.asyncio
async def test_restart_does_not_start_twenty_overdue_bursts_simultaneously():
    overdue = utcnow() - timedelta(hours=1)
    accounts = [make_account(index, next_cycle_at=overdue) for index in range(1, 21)]
    repo = FakeRepo(accounts)
    events = []
    manager = build_manager(repo, events)
    try:
        await manager.restore()
        await asyncio.wait_for(_wait_for_events(events, 20), timeout=5)
        assert all(account.scheduler_status == "RUNNING" for account in accounts)
        for previous, current in zip(
            [event for event in events if event[0] == "finish"],
            [event for event in events if event[0] == "start"][1:],
        ):
            assert current[2] >= previous[2]
    finally:
        await manager.shutdown()


@pytest.mark.asyncio
async def test_manual_run_uses_global_queue_and_cancellation_releases_lock():
    accounts = [make_account(1), make_account(2)]
    repo = FakeRepo(accounts)
    events = []
    manager = build_manager(repo, events, {1: 0.04, 2: 0.001})
    try:
        await manager.start_all_accounts(1)
        await manager.run_now(2)
        await asyncio.wait_for(_wait_for_events(events, 2), timeout=3)
        first_finish = next(event[2] for event in events if event[:2] == ("finish", 1))
        second_start = next(event[2] for event in events if event[:2] == ("start", 2))
        assert second_start >= first_finish
    finally:
        await manager.shutdown()


@pytest.mark.asyncio
async def test_cancelled_burst_releases_global_lock():
    account = make_account(1)
    repo = FakeRepo([account])
    started = asyncio.Event()

    class BlockingBurst:
        async def run_once(self):
            started.set()
            await asyncio.Event().wait()

    manager = BurstSchedulerManager(
        repo,
        client_factory=None,
        notify=None,
        worker_factory=lambda *_args: BlockingBurst(),
    )
    task = asyncio.create_task(manager._execute_burst(account))
    await asyncio.wait_for(started.wait(), timeout=1)
    task.cancel()
    await asyncio.gather(task, return_exceptions=True)
    assert not manager._global_burst_lock.locked()


@pytest.mark.asyncio
async def test_recalculate_after_account_add_and_remove_keeps_queue_order():
    accounts = [make_account(1), make_account(2)]
    repo = FakeRepo(accounts)
    manager = build_manager(repo, [])
    await manager.recalculate_schedule()
    repo.rows[3] = make_account(3)
    await manager.recalculate_schedule()
    assert [account.scheduler_position for account in repo.rows.values()] == [0, 1, 2]
    del repo.rows[2]
    await manager.recalculate_schedule()
    assert sorted(account.scheduler_position for account in repo.rows.values()) == [0, 1]
