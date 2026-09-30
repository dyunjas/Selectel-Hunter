import asyncio
import logging
from datetime import datetime, timedelta
from types import SimpleNamespace

from app.db.models import TaskStatus
from .account_worker import AccountBurstWorker

log = logging.getLogger(__name__)


def calculate_stagger(cooldown_seconds: float, active_account_count: int):
    if active_account_count <= 0:
        return None
    return float(cooldown_seconds) / active_account_count


class BurstSchedulerManager:
    def __init__(self, repo, client_factory, notify, default_cooldown=360, stagger_seconds=20):
        self.repo = repo
        self.client_factory = client_factory
        self.notify = notify
        self.default_cooldown = default_cooldown
        self.stagger_seconds = stagger_seconds
        self.account_workers = {}
        self.locks = {}
        self._start_guard = asyncio.Lock()

    def _lock(self, account_id):
        return self.locks.setdefault(account_id, asyncio.Lock())

    @staticmethod
    def _active(account):
        return account.enabled and account.scheduler_status in ("RUNNING", "IDLE") and (account.auto_start or account.scheduler_status == "RUNNING")

    async def recalculate_schedule(self):
        accounts = await self.repo.all_accounts()
        settings = await self.repo.scheduler_settings()
        active = [a for a in accounts if self._active(a)]
        active.sort(key=lambda a: (a.scheduler_position, a.id))
        total = len(active)
        # Cooldown is the period of a complete account pool. Each account gets
        # one slot in that period, so adding/removing accounts automatically
        # changes the distance between slots: 300/10=30s, 300/20=15s.
        pool_period = float(settings.burst_cooldown or self.default_cooldown)
        auto_spacing = calculate_stagger(pool_period, total) if total else self.stagger_seconds
        schedule_started = datetime.utcnow()
        for position, account in enumerate(active):
            stagger = account.manual_stagger if not account.auto_stagger and account.manual_stagger is not None else (
                auto_spacing if settings.auto_stagger and total else self.stagger_seconds
            )
            next_cycle = schedule_started + timedelta(seconds=position * stagger)
            await self.repo.update_account(account.id, scheduler_position=position, next_cycle_at=next_cycle)
        spacing = auto_spacing if settings.auto_stagger and total else self.stagger_seconds
        return [(a.id, i * spacing) for i, a in enumerate(active)]

    async def start_account(self, account_id, initial_delay=0):
        async with self._start_guard:
            current = self.account_workers.get(account_id)
            if current and not current.done():
                return current
            account = await self.repo.get_account(account_id)
            if not account:
                return None
            await self.repo.update_account(account_id, scheduler_status="RUNNING")
            # Include the newly activated account in the pool before starting
            # its worker. This keeps all accounts on the same live schedule.
            await self.recalculate_schedule()
            scheduled = await self.repo.get_account(account_id)
            if scheduled and scheduled.next_cycle_at:
                initial_delay = max(0.0, (scheduled.next_cycle_at - datetime.utcnow()).total_seconds())
            worker = AccountBurstWorker(account_id, self.repo, self.client_factory, self.notify, self._lock(account_id), initial_delay, self.recalculate_schedule)
            task = asyncio.create_task(worker.run(), name=f"account-burst-{account_id}")
            self.account_workers[account_id] = task
            task.add_done_callback(lambda done: self._worker_done(account_id, done))
            return task

    def _worker_done(self, account_id, task):
        if task.cancelled():
            return
        if task.exception():
            log.error("account burst crashed account_id=%s", account_id, exc_info=task.exception())
            asyncio.create_task(self.repo.update_account(account_id, scheduler_status="ERROR"))

    async def start(self):
        accounts = await self.repo.all_accounts()
        settings = await self.repo.scheduler_settings()
        active = [a for a in accounts if self._active(a)]
        active.sort(key=lambda a: (a.scheduler_position, a.id))
        total = len(active)
        for position, account in enumerate(active):
            await self.repo.update_account(account.id, scheduler_status="RUNNING", scheduler_position=position)
        await self.recalculate_schedule()
        for account in active:
            scheduled = await self.repo.get_account(account.id)
            delay = max(0.0, (scheduled.next_cycle_at - datetime.utcnow()).total_seconds()) if scheduled and scheduled.next_cycle_at else 0
            await self.start_account(account.id, delay)

    async def restore(self):
        return await self.start()

    async def pause_account(self, account_id):
        await self.repo.update_account(account_id, scheduler_status="PAUSED")
        await self._cancel(account_id)
        await self.recalculate_schedule()

    async def stop_account(self, account_id):
        await self.repo.update_account(account_id, scheduler_status="STOPPED", next_cycle_at=None)
        await self._cancel(account_id)
        await self.recalculate_schedule()

    async def resume_account(self, account_id):
        await self.repo.update_account(account_id, scheduler_status="RUNNING", cooldown_until=None, next_cycle_at=None)
        await self.recalculate_schedule()
        return await self.start_account(account_id)

    async def _cancel(self, account_id):
        task = self.account_workers.pop(account_id, None)
        if task and not task.done():
            task.cancel()
            await asyncio.gather(task, return_exceptions=True)

    async def run_now(self, account_id):
        account = await self.repo.get_account(account_id)
        if not account:
            return None
        task = self.account_workers.get(account_id)
        if task and not task.done():
            return False
        await self.repo.update_account(account_id, scheduler_status="RUNNING", next_cycle_at=None)
        return await self.start_account(account_id)

    async def get_schedule(self, user_id=None):
        accounts = await self.repo.all_accounts() if user_id is None else await self.repo.accounts(user_id)
        return sorted(accounts, key=lambda a: (a.scheduler_position, a.id))

    async def get_running_tasks(self):
        rows = []
        for account in await self.repo.all_accounts():
            if account.scheduler_status in ("RUNNING", "NETWORK_COOLDOWN"):
                subnets = await self.repo.enabled_targets()
                rows.append(SimpleNamespace(id=account.id, account_id=account.id, telegram_user_id=account.telegram_user_id, subnet_cidr=f"{len(subnets)} глобальных targets", status=account.scheduler_status, attempts=0, min_interval=30, max_interval=60, last_error=None))
        return rows

    async def get_task(self, task_id):
        task = await self.repo.get_task(task_id)
        if task:
            return task
        account = await self.repo.get_account(task_id)
        if not account:
            return None
        return SimpleNamespace(id=account.id, account_id=account.id, telegram_user_id=account.telegram_user_id, subnet_cidr="account burst", status=account.scheduler_status, attempts=0, min_interval=30, max_interval=60, last_error=None)

    async def start_pair(self, user_id, account, subnet, network_id, min_interval=None, max_interval=None):
        await self.repo.update_account(account.id, scheduler_status="RUNNING")
        await self.start_account(account.id)
        return account

    async def start_task(self, task):
        return await self.start_account(task.account_id)

    async def stop_task(self, task_id):
        task = await self.repo.get_task(task_id)
        await self.stop_account(task.account_id if task else task_id)
        if task:
            await self.repo.update_task(task_id, status=TaskStatus.STOPPED.value)

    async def pause_task(self, task_id):
        task = await self.repo.get_task(task_id)
        await self.pause_account(task.account_id if task else task_id)
        if task:
            await self.repo.update_task(task_id, status=TaskStatus.PAUSED.value)

    async def resume_task(self, task_id):
        task = await self.repo.get_task(task_id)
        await self.resume_account(task.account_id if task else task_id)
        return task or await self.get_task(task_id)

    async def restore_tasks(self):
        return await self.restore()

    async def shutdown(self):
        tasks = list(self.account_workers.values())
        self.account_workers.clear()
        for task in tasks:
            task.cancel()
        if tasks:
            await asyncio.gather(*tasks, return_exceptions=True)


AccountSchedulerManager = BurstSchedulerManager
