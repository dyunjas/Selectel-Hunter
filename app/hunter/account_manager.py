import asyncio
import logging
from types import SimpleNamespace
from app.db.models import TaskStatus
from .account_worker import AccountWorker

log = logging.getLogger(__name__)


class AccountSchedulerManager:
    def __init__(self, repo, client_factory, notify, default_interval=150):
        self.repo = repo
        self.client_factory = client_factory
        self.notify = notify
        self.default_interval = default_interval
        self.workers = {}
        self.locks = {}
        self._start_guard = asyncio.Lock()

    def _lock(self, account_id):
        return self.locks.setdefault(account_id, asyncio.Lock())

    async def start_account(self, account_id, initial_delay=0):
        async with self._start_guard:
            current = self.workers.get(account_id)
            if current and not current.done():
                return current
            account = await self.repo.get_account(account_id)
            if not account:
                return None
            await self.repo.update_account(account_id, scheduler_status="RUNNING")
            worker = AccountWorker(account_id, self.repo, self.client_factory, self.notify, self._lock(account_id), initial_delay)
            task = asyncio.create_task(worker.run(), name=f"account-scheduler-{account_id}")
            self.workers[account_id] = task
            task.add_done_callback(lambda done: self._worker_done(account_id, done))
            return task

    def _worker_done(self, account_id, task):
        if task.cancelled():
            return
        if task.exception():
            log.error("account scheduler crashed account_id=%s", account_id, exc_info=task.exception())
            asyncio.create_task(self.repo.update_account(account_id, scheduler_status="ERROR"))

    async def stop_account(self, account_id):
        await self.repo.update_account(account_id, scheduler_status="STOPPED", next_request_at=None)
        task = self.workers.pop(account_id, None)
        if task and not task.done():
            task.cancel()
            await asyncio.gather(task, return_exceptions=True)

    async def pause_account(self, account_id):
        await self.repo.update_account(account_id, scheduler_status="PAUSED")
        task = self.workers.pop(account_id, None)
        if task and not task.done():
            task.cancel()
            await asyncio.gather(task, return_exceptions=True)

    async def resume_account(self, account_id):
        await self.repo.update_account(account_id, scheduler_status="RUNNING", cooldown_until=None)
        return await self.start_account(account_id)

    async def start_pair(self, user_id, account, subnet, network_id, min_interval=None, max_interval=None):
        values = {"scheduler_status": "RUNNING"}
        if min_interval is not None:
            values["min_interval"] = max(30, int(min_interval))
        if max_interval is not None:
            values["max_interval"] = max(values.get("min_interval", max(30, account.min_interval)), int(max_interval))
        await self.repo.update_account(account.id, **values)
        await self.start_account(account.id)
        return account

    async def restore_all(self):
        accounts = await self.repo.all_accounts()
        active = [a for a in accounts if a.auto_start or a.scheduler_status == "RUNNING"]
        if not active:
            return
        spacing = max(1.0, self.default_interval / len(active))
        for number, account in enumerate(active):
            await self.repo.update_account(account.id, scheduler_status="RUNNING", next_request_at=None)
            await self.start_account(account.id, initial_delay=number * spacing)

    async def get_status(self, user_id=None):
        accounts = await self.repo.all_accounts() if user_id is None else await self.repo.accounts(user_id)
        return accounts

    async def get_running_tasks(self):
        # Compatibility rows for the old UI; the real background unit is the account.
        accounts = await self.repo.all_accounts()
        rows = []
        for account in accounts:
            if account.scheduler_status not in ("RUNNING", "NETWORK_COOLDOWN"):
                continue
            subnets = await self.repo.enabled_subnets(account.id)
            rows.append(SimpleNamespace(
                id=account.id, account_id=account.id, telegram_user_id=account.telegram_user_id,
                subnet_cidr=f"{len(subnets)} целевых подсетей", status=account.scheduler_status,
                attempts=0, min_interval=account.min_interval, max_interval=account.max_interval,
                last_error=None,
            ))
        return rows

    async def get_task(self, task_id):
        task = await self.repo.get_task(task_id)
        if task:
            return task
        account = await self.repo.get_account(task_id)
        if not account:
            return None
        subnets = await self.repo.enabled_subnets(account.id)
        return SimpleNamespace(
            id=account.id, account_id=account.id, telegram_user_id=account.telegram_user_id,
            subnet_cidr=f"{len(subnets)} целевых подсетей", status=account.scheduler_status,
            attempts=0, min_interval=account.min_interval, max_interval=account.max_interval,
            last_error=None,
        )

    async def start_task(self, task):
        return await self.start_account(task.account_id)

    async def stop_task(self, task_id):
        task = await self.repo.get_task(task_id)
        if not task and await self.repo.get_account(task_id):
            await self.stop_account(task_id)
            return
        if task:
            await self.stop_account(task.account_id)
            await self.repo.update_task(task_id, status=TaskStatus.STOPPED.value)

    async def pause_task(self, task_id):
        task = await self.repo.get_task(task_id)
        if not task and await self.repo.get_account(task_id):
            await self.pause_account(task_id)
            return
        if task:
            await self.pause_account(task.account_id)
            await self.repo.update_task(task_id, status=TaskStatus.PAUSED.value)

    async def resume_task(self, task_id):
        task = await self.repo.get_task(task_id)
        if not task and await self.repo.get_account(task_id):
            await self.resume_account(task_id)
            return await self.get_task(task_id)
        if task:
            await self.resume_account(task.account_id)
            await self.repo.update_task(task_id, status=TaskStatus.RUNNING.value)
        return task

    async def restore_tasks(self):
        return await self.restore_all()

    async def shutdown(self):
        tasks = list(self.workers.values())
        self.workers.clear()
        for task in tasks:
            task.cancel()
        if tasks:
            await asyncio.gather(*tasks, return_exceptions=True)
