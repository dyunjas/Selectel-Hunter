import asyncio
import logging
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

from app.db.models import TaskStatus
from .account_worker import AccountBurstWorker, next_scheduled_cycle

log = logging.getLogger(__name__)


def calculate_stagger(cooldown_seconds: float, active_account_count: int):
    if active_account_count <= 0:
        return None
    return float(cooldown_seconds) / active_account_count


def _utcnow():
    return datetime.now(timezone.utc).replace(tzinfo=None)


class BurstSchedulerManager:
    """Dispatch staggered BURST slots without waiting for other accounts' replies."""

    def __init__(
        self,
        repo,
        client_factory,
        notify,
        default_cooldown=360,
        stagger_seconds=20,
        worker_factory=AccountBurstWorker,
    ):
        self.repo = repo
        self.client_factory = client_factory
        self.notify = notify
        self.default_cooldown = default_cooldown
        self.stagger_seconds = stagger_seconds
        self.worker_factory = worker_factory
        # At most one in-flight BURST per account; starts use a global cadence.
        self.account_workers = {}
        self._last_dispatch_at = None
        self._running_started_at = {}
        self._scheduler_task = None
        self._scheduler_stop = False
        self._wake_event = asyncio.Event()
        self._schedule_guard = asyncio.Lock()
        self._start_guard = asyncio.Lock()

    @staticmethod
    def _active(account):
        return (
            account.enabled
            and account.scheduler_status in {"RUNNING", "RATE_LIMIT_COOLDOWN", "IDLE"}
            and (account.auto_start or account.scheduler_status != "IDLE")
        )

    @staticmethod
    def _eligible(account):
        return account.enabled and account.scheduler_status in {"RUNNING", "RATE_LIMIT_COOLDOWN"}

    def _wake(self):
        self._wake_event.set()

    @staticmethod
    def _ready_at(account, period):
        limits = [account.next_cycle_at]
        if account.scheduler_status == "RATE_LIMIT_COOLDOWN":
            limits.append(account.cooldown_until)
        return max((value for value in limits if value), default=datetime.min)

    @staticmethod
    def _activation_status(account):
        if account.scheduler_status == "RATE_LIMIT_COOLDOWN" and account.cooldown_until and account.cooldown_until > _utcnow():
            return "RATE_LIMIT_COOLDOWN"
        return "RUNNING"

    async def _ensure_scheduler(self):
        if self._scheduler_task and not self._scheduler_task.done():
            self._wake()
            return self._scheduler_task
        self._scheduler_stop = False
        self._scheduler_task = asyncio.create_task(self._scheduler_loop(), name="global-burst-scheduler")
        return self._scheduler_task

    async def recalculate_schedule(self, reset=True):
        """Rebuild start times while preserving in-flight account cycles."""
        async with self._schedule_guard:
            accounts = await self.repo.all_accounts()
            settings = await self.repo.scheduler_settings()
            active = [account for account in accounts if self._active(account)]
            active.sort(key=lambda account: (account.scheduler_position, account.id))
            total = len(active)
            period = float(settings.burst_cooldown or self.default_cooldown or 1)
            spacing = calculate_stagger(period, total) if total else self.stagger_seconds
            now = _utcnow()
            entries = []
            for position, account in enumerate(active):
                previous = account.next_cycle_at
                cooldown = account.cooldown_until if account.scheduler_status == "RATE_LIMIT_COOLDOWN" else None
                if account.id in self.account_workers:
                    next_cycle = previous or now
                elif not reset and previous and previous > now:
                    next_cycle = previous
                else:
                    calculated = now + timedelta(seconds=position * (spacing or self.stagger_seconds))
                    next_cycle = calculated
                if account.id not in self.account_workers:
                    minimum = account.last_cycle_started_at + timedelta(seconds=period) if account.last_cycle_started_at else datetime.min
                    next_cycle = max(next_cycle, cooldown or datetime.min, minimum)
                values = {"scheduler_position": position, "next_cycle_at": next_cycle}
                if account.scheduler_status != "RATE_LIMIT_COOLDOWN":
                    values["cooldown_until"] = None
                entries.append((account.id, values))
            if entries:
                await self.repo.apply_schedule(entries)
        self._wake()
        return [(account_id, index * (spacing or self.stagger_seconds)) for index, (account_id, _) in enumerate(entries)]

    async def start_account(self, account_id, initial_delay=0, reschedule=True):
        async with self._start_guard:
            account = await self.repo.get_account(account_id)
            if not account:
                return None
            await self.repo.update_account(account_id, scheduler_status=self._activation_status(account))
            if reschedule:
                await self.recalculate_schedule(reset=True)
            elif not (await self.repo.get_account(account_id)).next_cycle_at:
                await self.repo.update_account(
                    account_id,
                    next_cycle_at=_utcnow() + timedelta(seconds=max(0, initial_delay)),
                )
            await self._ensure_scheduler()
            self._wake()
            return self._scheduler_task

    async def _recover_interrupted_cycles(self, accounts, cooldown):
        """Avoid immediately replaying a BURST interrupted by a restart."""
        now = _utcnow()
        for account in accounts:
            started = account.last_cycle_started_at
            finished = account.last_cycle_finished_at
            if started and (not finished or started > finished):
                await self.repo.update_account(
                    account.id,
                    next_cycle_at=max(now, started + timedelta(seconds=cooldown)),
                )

    async def start(self):
        accounts = await self.repo.all_accounts()
        settings = await self.repo.scheduler_settings()
        active = [account for account in accounts if self._active(account)]
        active.sort(key=lambda account: (account.scheduler_position, account.id))
        for position, account in enumerate(active):
            values = {"scheduler_position": position}
            if account.scheduler_status == "IDLE":
                values["scheduler_status"] = "RUNNING"
            await self.repo.update_account(account.id, **values)
        await self._recover_interrupted_cycles(active, int(settings.burst_cooldown or self.default_cooldown or 1))
        await self.recalculate_schedule(reset=True)
        await self._ensure_scheduler()

    async def restore(self):
        return await self.start()

    async def pause_account(self, account_id):
        await self.repo.update_account(account_id, scheduler_status="PAUSED", next_cycle_at=None)
        self._wake()
        await self.recalculate_schedule(reset=True)

    async def stop_account(self, account_id):
        await self.repo.update_account(account_id, scheduler_status="STOPPED", next_cycle_at=None)
        self._wake()
        await self.recalculate_schedule(reset=True)

    async def resume_account(self, account_id):
        account = await self.repo.get_account(account_id)
        if not account:
            return None
        await self.repo.update_account(account_id, scheduler_status=self._activation_status(account), next_cycle_at=_utcnow())
        await self.recalculate_schedule(reset=True)
        await self._ensure_scheduler()
        self._wake()
        return self._scheduler_task

    async def run_now(self, account_id):
        account = await self.repo.get_account(account_id)
        if not account:
            return None
        if account_id in self.account_workers:
            return False
        await self.repo.update_account(account_id, scheduler_status=self._activation_status(account), next_cycle_at=_utcnow())
        await self._ensure_scheduler()
        self._wake()
        return self._scheduler_task

    async def start_all_accounts(self, user_id):
        """Activate all owned accounts, then rebuild the queue exactly once."""
        started = 0
        failed = []
        for account in await self.repo.accounts(user_id):
            try:
                await self.repo.update_account(account.id, scheduler_status=self._activation_status(account))
                started += 1
            except Exception as exc:
                failed.append((account.display_name, str(exc)))
                log.exception("failed to activate account account_id=%s", account.id)
        if started:
            await self.recalculate_schedule(reset=True)
            await self._ensure_scheduler()
        self._wake()
        return started, failed

    async def _scheduler_loop(self):
        while not self._scheduler_stop:
            try:
                self._wake_event.clear()
                accounts = await self.repo.all_accounts()
                settings = await self.repo.scheduler_settings()
                period = max(1, float(settings.burst_cooldown or self.default_cooldown or 1))
                active_count = sum(self._active(account) for account in accounts)
                spacing = calculate_stagger(period, active_count) or period
                eligible = [account for account in accounts if self._eligible(account) and account.id not in self.account_workers]
                now = _utcnow()
                slot = self._last_dispatch_at + timedelta(seconds=spacing) if self._last_dispatch_at else now
                due = [account for account in eligible if self._ready_at(account, period) <= now]
                if due and slot <= now:
                    due.sort(
                        key=lambda item: (item.next_cycle_at or datetime.min, item.scheduler_position, item.id),
                    )
                    account = due[0]
                    # Advance on the fixed slot grid, skipping missed slots.
                    self._last_dispatch_at = slot + timedelta(seconds=int((now - slot).total_seconds() / spacing) * spacing)
                    task = asyncio.create_task(self._execute_burst(account), name=f"account-burst-{account.id}")
                    self.account_workers[account.id] = task
                    task.add_done_callback(lambda completed, account_id=account.id: self._burst_done(account_id, completed))
                    continue

                future = [max(self._ready_at(account, period), slot) for account in eligible]
                timeout = max(0.001, (min(future) - now).total_seconds()) if future else 60.0
                try:
                    await asyncio.wait_for(self._wake_event.wait(), timeout=timeout)
                except asyncio.TimeoutError:
                    pass
            except asyncio.CancelledError:
                raise
            except Exception:
                # A broken iteration must not stop all account searches.
                log.exception("global burst scheduler iteration failed")
                await asyncio.sleep(1)

    def _burst_done(self, account_id, task):
        if self.account_workers.get(account_id) is task:
            self.account_workers.pop(account_id, None)
            self._running_started_at.pop(account_id, None)
        if not task.cancelled() and task.exception():
            log.error("account burst task failed account_id=%s", account_id,
                      exc_info=task.exception())
        self._wake()

    async def _execute_burst(self, account):
        try:
            current = await self.repo.get_account(account.id)
            if not current or not self._eligible(current):
                return
            settings = await self.repo.scheduler_settings()
            period = max(1, float(settings.burst_cooldown or self.default_cooldown or 1))
            if self._ready_at(current, period) > _utcnow():
                return
            if current.scheduler_status == "RATE_LIMIT_COOLDOWN":
                await self.repo.update_account(current.id, scheduler_status="RUNNING", cooldown_until=None)
                current = await self.repo.get_account(current.id)
            self._running_started_at[current.id] = _utcnow()
            planned = current.next_cycle_at or self._running_started_at[current.id]
            worker = self.worker_factory(current.id, self.repo, self.client_factory, self.notify, asyncio.Lock())
            result = await worker.run_once()
            finished = _utcnow()
            latest = await self.repo.get_account(current.id)
            if not latest:
                return
            settings = await self.repo.scheduler_settings()
            period = max(1, int(settings.burst_cooldown or self.default_cooldown or 1))
            next_cycle = next_scheduled_cycle(planned, finished, period, latest.next_cycle_at)
            values = {"last_cycle_finished_at": finished}
            if result in {"finished", "cooldown"} and self._eligible(latest):
                if latest.scheduler_status == "RATE_LIMIT_COOLDOWN":
                    next_cycle = max(next_cycle, latest.cooldown_until or datetime.min)
                else:
                    values["cooldown_until"] = None
                values["next_cycle_at"] = next_cycle
            await self.repo.update_account(current.id, **values)
        except asyncio.CancelledError:
            raise
        except Exception:
            log.exception("account burst execution failed account_id=%s", account.id)
            latest = await self.repo.get_account(account.id)
            if latest:
                settings = await self.repo.scheduler_settings()
                period = max(1, int(settings.burst_cooldown or self.default_cooldown or 1))
                finished = _utcnow()
                next_cycle = next_scheduled_cycle(account.next_cycle_at or finished, finished, period)
                values = {"last_cycle_finished_at": finished}
                if self._eligible(latest):
                    values["next_cycle_at"] = max(next_cycle, latest.cooldown_until or datetime.min) if latest.scheduler_status == "RATE_LIMIT_COOLDOWN" else next_cycle
                await self.repo.update_account(account.id, **values)

    async def get_schedule(self, user_id=None):
        accounts = await self.repo.all_accounts() if user_id is None else await self.repo.accounts(user_id)
        return sorted(accounts, key=lambda account: (account.next_cycle_at or datetime.max, account.scheduler_position, account.id))

    async def get_scheduler_snapshot(self, user_id=None):
        accounts = await self.get_schedule(user_id)
        settings = await self.repo.scheduler_settings()
        period = max(1, float(settings.burst_cooldown or self.default_cooldown or 1))
        now = _utcnow()
        queue = []
        running = [account.id for account in accounts if account.id in self.account_workers]
        for account in accounts:
            status = account.scheduler_status
            if account.scheduler_status == "RATE_LIMIT_COOLDOWN" and account.cooldown_until:
                reason = f"429: cooldown до {account.cooldown_until:%H:%M:%S} UTC"
            elif account.scheduler_status == "BLOCKED":
                reason = "остановлен из-за ошибки доступа"
            elif account.scheduler_status == "PAUSED":
                reason = "пауза"
            elif account.id in self.account_workers:
                reason = "выполняет запросы"
            elif self._eligible(account) and self._ready_at(account, period) > now:
                status = "WAITING"
                reason = "ждёт своего времени запуска"
            elif self._eligible(account):
                status = "WAITING"
                reason = "ждёт очереди"
            else:
                reason = "не запланирован"
            queue.append({"account": account, "reason": reason, "status": status})
        return {
            "running_account_ids": running,
            "current_account_id": running[0] if running else None,
            "current_started_at": self._running_started_at.get(running[0]) if running else None,
            "current_finished_at": None,
            "queue": queue,
        }

    async def get_running_tasks(self):
        rows = []
        for account in await self.repo.all_accounts():
            if account.scheduler_status in ("RUNNING", "NETWORK_COOLDOWN", "RATE_LIMIT_COOLDOWN"):
                subnets = await self.repo.enabled_targets()
                rows.append(SimpleNamespace(id=account.id, account_id=account.id, telegram_user_id=account.telegram_user_id, subnet_cidr=f"{len(subnets)} targets", status=account.scheduler_status, attempts=0, min_interval=30, max_interval=60, last_error=None))
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
        self._scheduler_stop = True
        self._wake()
        task = self._scheduler_task
        self._scheduler_task = None
        if task and not task.done():
            task.cancel()
            await asyncio.gather(task, return_exceptions=True)
        workers = list(self.account_workers.values())
        for worker in workers:
            worker.cancel()
        if workers:
            await asyncio.gather(*workers, return_exceptions=True)
        self.account_workers.clear()
        self._running_started_at.clear()


AccountSchedulerManager = BurstSchedulerManager
