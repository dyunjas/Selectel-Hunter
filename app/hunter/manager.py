import asyncio, logging
from app.db.models import TaskStatus
from .worker import HunterWorker
from .account_manager import AccountSchedulerManager

# New code should use AccountSchedulerManager.  Keep TaskManager available for
# older integrations while the database is migrated in place.

log = logging.getLogger(__name__)


class TaskManager:
    def __init__(self, repo, client_factory, notify): self.repo, self.client_factory, self.notify, self.tasks = repo, client_factory, notify, {}

    async def start_task(self, task):
        if task.id in self.tasks and not self.tasks[task.id].done(): return task
        client = await self.client_factory(task.account_id)
        self.tasks[task.id] = asyncio.create_task(HunterWorker(task, self.repo, client, self.notify).run(), name=f"hunter-{task.id}")
        self.tasks[task.id].add_done_callback(lambda _: asyncio.create_task(self._recover_if_needed(task.id)))
        return task

    async def _recover_if_needed(self, task_id):
        try:
            task = await self.repo.get_task(task_id)
            if task and task.status == TaskStatus.RUNNING.value:
                await asyncio.sleep(2)
                log.warning("recovering unexpectedly finished task_id=%s", task_id)
                await self.start_task(task)
        except Exception:
            log.exception("task recovery failed task_id=%s", task_id)

    async def start_pair(self, user_id, account, subnet, network_id, min_interval=None, max_interval=None):
        min_interval = account.min_interval if min_interval is None else min_interval
        max_interval = account.max_interval if max_interval is None else max_interval
        task = await self.repo.create_task(telegram_user_id=user_id, account_id=account.id, subnet_id=subnet.subnet_id, subnet_cidr=subnet.cidr, network_id=network_id, region=account.region, status=TaskStatus.RUNNING.value, min_interval=max(3, min_interval), max_interval=max(max(3, min_interval), max_interval))
        await self.start_task(task); return task

    async def stop_task(self, task_id):
        await self.repo.update_task(task_id, status=TaskStatus.STOPPED.value); await self._cancel(task_id)
    async def pause_task(self, task_id): await self.repo.update_task(task_id, status=TaskStatus.PAUSED.value); await self._cancel(task_id)
    async def resume_task(self, task_id): task = await self.repo.get_task(task_id); await self.repo.update_task(task_id, status=TaskStatus.RUNNING.value); await self.start_task(task); return task
    async def _cancel(self, task_id):
        running = self.tasks.pop(task_id, None)
        if running: running.cancel(); await asyncio.gather(running, return_exceptions=True)
    async def restore_tasks(self):
        for task in await self.repo.running_tasks(): await self.start_task(task)
    async def shutdown(self):
        for task_id in list(self.tasks): await self._cancel(task_id)
    async def stop_account(self, account_id):
        task_ids = [task.id for task in await self.repo.running_tasks() if task.account_id == account_id]
        for task_id in task_ids: await self.stop_task(task_id)
    async def get_task(self, task_id): return await self.repo.get_task(task_id)
    async def get_running_tasks(self): return await self.repo.running_tasks()


LegacyTaskManager = TaskManager
TaskManager = AccountSchedulerManager
