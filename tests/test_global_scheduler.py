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
async def test_twenty_accounts_start_on_schedule_while_others_wait_for_replies():
    accounts = [make_account(index) for index in range(1, 21)]
    repo = FakeRepo(accounts)
    events = []
    durations = {index: 1.1 for index in range(1, 21)}
    manager = build_manager(repo, events, durations)
    try:
        started, failed = await manager.start_all_accounts(1)
        assert started == 20
        assert failed == []
        await asyncio.wait_for(_wait_for_events(events, 20), timeout=5)
        starts = [event for event in events if event[0] == "start"][:20]
        finishes = [event for event in events if event[0] == "finish"][:20]
        assert len(starts) == len(finishes) == 20
        assert [event[1] for event in starts] == list(range(1, 21))
        assert starts[-1][2] < finishes[0][2]
        assert starts[-1][2] - starts[0][2] < 1.08
    finally:
        await manager.shutdown()


async def _wait_for_events(events, count):
    while len([event for event in events if event[0] == "finish"]) < count:
        await asyncio.sleep(0.001)


@pytest.mark.asyncio
async def test_repeated_bursts_keep_start_cadence_without_finish_cooldown():
    accounts = [make_account(1), make_account(2)]
    repo = FakeRepo(accounts, cooldown=1)
    events = []
    manager = build_manager(repo, events, {1: 0.3, 2: 0.15})
    try:
        await manager.start_all_accounts(1)
        await asyncio.wait_for(_wait_for_events(events, 5), timeout=4)
        starts = [event for event in events if event[0] == "start"]
        assert [event[1] for event in starts[:5]] == [1, 2, 1, 2, 1]
        for index, event in enumerate(starts[:5]):
            assert abs(event[2] - starts[0][2] - index * 0.5) < 0.12
        assert all(account.cooldown_until is None for account in accounts)
    finally:
        await manager.shutdown()


@pytest.mark.asyncio
@pytest.mark.parametrize("action", ["run_now", "resume_account", "start_account", "start_all_accounts"])
async def test_manual_actions_preserve_only_rate_limit_cooldown(action):
    account = make_account(1, status="RATE_LIMIT_COOLDOWN")
    until = utcnow() + timedelta(seconds=10)
    account.cooldown_until = until
    account.next_cycle_at = until
    repo = FakeRepo([account], cooldown=1)
    events = []
    manager = build_manager(repo, events)
    try:
        await getattr(manager, action)(1)
        await manager.recalculate_schedule(reset=True)
        await asyncio.sleep(0.02)
        assert events == []
        assert account.scheduler_status == "RATE_LIMIT_COOLDOWN"
        assert account.cooldown_until == until
    finally:
        await manager.shutdown()


@pytest.mark.asyncio
async def test_restore_discards_old_finish_based_cooldown():
    account = make_account(1)
    account.last_cycle_started_at = utcnow() - timedelta(seconds=2)
    account.last_cycle_finished_at = utcnow() - timedelta(seconds=0.1)
    account.cooldown_until = utcnow() + timedelta(seconds=10)
    account.next_cycle_at = account.cooldown_until
    repo = FakeRepo([account], cooldown=1)
    manager = build_manager(repo, [])
    await manager.recalculate_schedule(reset=True)
    assert account.cooldown_until is None
    assert account.next_cycle_at < account.last_cycle_finished_at + timedelta(seconds=1)


@pytest.mark.asyncio
async def test_long_burst_does_not_delay_next_account_and_respects_own_cooldown():
    accounts = [make_account(1), make_account(2)]
    repo = FakeRepo(accounts)
    events = []
    manager = build_manager(repo, events, {1: 0.9, 2: 0.001})
    try:
        await manager.start_all_accounts(1)
        await asyncio.wait_for(_wait_for_events(events, 2), timeout=3)
        first_finish = next(event[2] for event in events if event[:2] == ("finish", 1))
        second_start = next(event[2] for event in events if event[:2] == ("start", 2))
        assert second_start < first_finish
        assert repo.rows[1].next_cycle_at < repo.rows[1].last_cycle_finished_at + timedelta(seconds=1)
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
        assert account.next_cycle_at >= account.cooldown_until
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
        starts = [event for event in events if event[0] == "start"]
        assert [event[1] for event in starts] == list(range(1, 21))
        # Restart still staggers overdue accounts; completion order is irrelevant.
        assert starts[-1][2] - starts[0][2] >= 0.8
        for index, event in enumerate(starts):
            assert abs((event[2] - starts[0][2]) - index * 0.05) < 0.15
    finally:
        await manager.shutdown()


@pytest.mark.asyncio
async def test_manual_run_starts_while_another_account_waits():
    accounts = [make_account(1), make_account(2)]
    repo = FakeRepo(accounts)
    events = []
    manager = build_manager(repo, events, {1: 0.9, 2: 0.001})
    try:
        await manager.start_all_accounts(1)
        await manager.run_now(2)
        await asyncio.wait_for(_wait_for_events(events, 2), timeout=3)
        first_finish = next(event[2] for event in events if event[:2] == ("finish", 1))
        second_start = next(event[2] for event in events if event[:2] == ("start", 2))
        assert second_start < first_finish
    finally:
        await manager.shutdown()


@pytest.mark.asyncio
async def test_shutdown_cancels_all_inflight_account_bursts():
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
    await manager.start_all_accounts(1)
    await asyncio.wait_for(started.wait(), timeout=1)
    task = manager.account_workers[1]
    await manager.shutdown()
    assert task.cancelled()
    assert manager.account_workers == {}
    assert manager._running_started_at == {}


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


@pytest.mark.asyncio
async def test_pending_response_does_not_delay_later_accounts_or_duplicate_cycle():
    now = utcnow()
    accounts = [make_account(index, next_cycle_at=now + timedelta(seconds=(index - 1) / 3)) for index in range(1, 4)]
    repo = FakeRepo(accounts, cooldown=1)
    first_started = asyncio.Event()
    all_started = asyncio.Event()
    release = asyncio.Event()
    starts = []

    class PendingBurst:
        def __init__(self, account_id):
            self.account_id = account_id

        async def run_once(self):
            starts.append(self.account_id)
            if self.account_id == 1:
                first_started.set()
            if len(starts) == 3:
                all_started.set()
            await release.wait()
            return "finished"

    manager = BurstSchedulerManager(repo, None, None, worker_factory=lambda account_id, *_: PendingBurst(account_id))
    try:
        await manager._ensure_scheduler()
        await asyncio.wait_for(first_started.wait(), timeout=1)
        assert await manager.run_now(1) is False
        await asyncio.wait_for(all_started.wait(), timeout=2)
        assert starts == [1, 2, 3]
        assert set(manager.account_workers) == {1, 2, 3}
        snapshot = await manager.get_scheduler_snapshot(1)
        assert set(snapshot["running_account_ids"]) == {1, 2, 3}
        assert all(item["reason"] == "выполняет запросы" for item in snapshot["queue"])
        previous = [account.next_cycle_at for account in accounts]
        await manager.recalculate_schedule(reset=True)
        assert [account.next_cycle_at for account in accounts] == previous
        for _ in range(5):
            manager._wake()
            await asyncio.sleep(0)
        assert starts == [1, 2, 3]
    finally:
        await manager.shutdown()


@pytest.mark.asyncio
async def test_worker_failure_does_not_stop_another_account_or_revive_paused_account():
    accounts = [make_account(1), make_account(2)]
    repo = FakeRepo(accounts, cooldown=1)
    completed = asyncio.Event()

    class FailingBurst:
        def __init__(self, account_id):
            self.account_id = account_id

        async def run_once(self):
            if self.account_id == 1:
                await repo.update_account(1, scheduler_status="PAUSED", next_cycle_at=None)
                raise RuntimeError("request failed")
            completed.set()
            return "finished"

    manager = BurstSchedulerManager(repo, None, None, worker_factory=lambda account_id, *_: FailingBurst(account_id))
    try:
        await manager._ensure_scheduler()
        await asyncio.wait_for(completed.wait(), timeout=3)
        assert accounts[0].scheduler_status == "PAUSED"
        assert accounts[0].next_cycle_at is None
        assert accounts[1].scheduler_status == "RUNNING"
    finally:
        await manager.shutdown()
