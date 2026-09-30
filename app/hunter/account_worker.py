import asyncio
import logging
import time
from datetime import datetime, timedelta
from types import SimpleNamespace

from app.db.models import TaskStatus
from app.selectel.errors import ErrorType, SelectelError

log = logging.getLogger(__name__)


def utcnow():
    return datetime.utcnow()


class AccountBurstWorker:
    """Executes one account burst sequentially, then waits for its cooldown."""

    def __init__(self, account_id, repo, client_factory, notify, lock=None, initial_delay=0, on_schedule_change=None):
        self.account_id = account_id
        self.repo = repo
        self.client_factory = client_factory
        self.notify = notify
        self.lock = lock or asyncio.Lock()
        self.initial_delay = max(0.0, initial_delay)
        self.on_schedule_change = on_schedule_change
        self.attempt_count = 0
        self.cycle_id = 0

    async def _state(self, **values):
        await self.repo.update_account(self.account_id, **values)

    async def _recalculate(self):
        if self.on_schedule_change:
            await self.on_schedule_change()

    async def _wait(self, when):
        if when:
            await asyncio.sleep(max(0.0, (when - utcnow()).total_seconds()))

    async def _notify(self, account, subnet, event, ip=None, fip_id=None, elapsed=0):
        subject = SimpleNamespace(
            account_id=account.id, telegram_user_id=account.telegram_user_id,
            subnet_cidr=subnet.cidr, subnet_id=subnet.subnet_id,
            attempts=self.attempt_count, region=subnet.region,
        )
        try:
            await self.notify(subject, ip, fip_id, elapsed, event)
        except Exception:
            log.exception("burst notification failed account_id=%s", self.account_id)

    async def _record(self, account, subnet, result, started, exc=None):
        await self.repo.record_attempt(
            account_id=account.id, subnet_id=subnet.subnet_id, cidr=subnet.cidr,
            result=result, http_status=getattr(exc, "status", None),
            error_type=getattr(getattr(exc, "kind", None), "value", None),
            elapsed_ms=int((time.monotonic() - started) * 1000),
        )
        log.info(
            "account=%s cycle=%s subnet=%s region=%s result=%s http=%s attempt=%s elapsed_ms=%s",
            account.id, self.cycle_id, subnet.cidr, subnet.region, result,
            getattr(exc, "status", None), self.attempt_count,
            int((time.monotonic() - started) * 1000),
        )

    async def _found(self, account, subnet, result, started):
        ip, fip_id = result.get("floating_ip_address"), result.get("id")
        elapsed = time.monotonic() - started
        await self._record(account, subnet, "FOUND", started)
        task = await self.repo.create_task(
            telegram_user_id=account.telegram_user_id, account_id=account.id,
            subnet_id=subnet.subnet_id, subnet_cidr=subnet.cidr, network_id="",
            region=subnet.region, status=TaskStatus.FOUND.value,
            attempts=self.attempt_count, min_interval=30, max_interval=60,
            floating_ip_address=ip, floating_ip_id=fip_id,
            elapsed_seconds=elapsed, finished_at=utcnow(),
        )
        await self.repo.add_found(
            account_id=account.id, hunter_task_id=task.id, subnet_id=subnet.subnet_id,
            subnet_cidr=subnet.cidr, region=subnet.region,
            floating_ip_id=fip_id or "", floating_ip_address=ip or "",
            attempts=self.attempt_count,
        )
        await self.repo.disable_subnet(account.id, subnet.subnet_id)
        await self.repo.disable_account_target(account.id, subnet.subnet_id)
        await self._notify(account, subnet, "FOUND", ip, fip_id, elapsed)

    async def _error(self, account, subnet, started, exc):
        kind = exc.kind.value
        await self._record(account, subnet, kind, started, exc)
        now = utcnow()
        if kind == ErrorType.NO_FREE_IP.value:
            await self._state(consecutive_network_errors=0)
            await self._notify(account, subnet, f"NO_FREE_IP: {exc}")
            return "continue"
        if kind in (ErrorType.PERMISSION_ERROR.value, ErrorType.AUTH_ERROR.value):
            await self._state(scheduler_status="BLOCKED", next_cycle_at=None, cooldown_until=None)
            await self._recalculate()
            await self._notify(account, subnet, f"{kind}: HTTP {exc.status or '—'}: {exc}")
            return "stop"
        if kind == ErrorType.RATE_LIMIT.value:
            delay = exc.retry_after or 300
            until = now + timedelta(seconds=float(delay))
            await self._state(scheduler_status="RATE_LIMIT_COOLDOWN", cooldown_until=until, next_cycle_at=until)
            await self._recalculate()
            await self._notify(account, subnet, f"RATE_LIMIT: HTTP 429: cooldown {int(delay)} сек. — {exc}")
            return "cooldown"
        if kind == ErrorType.NETWORK_ERROR.value:
            errors = int(account.consecutive_network_errors or 0) + 1
            scheduler_settings = await self.repo.scheduler_settings()
            limit = max(1, int(scheduler_settings.errors_before_disable or 30))
            if errors >= limit:
                await self._state(scheduler_status="ERROR", consecutive_network_errors=errors, next_cycle_at=None)
                await self._recalculate()
                await self._notify(account, subnet, f"NETWORK_ERROR: отключение после {errors} ошибок: {exc}")
                return "stop"
            await self._state(consecutive_network_errors=errors)
            log.warning("network error account=%s cycle=%s count=%s", account.id, self.cycle_id, errors)
            return "continue"
        await self._notify(account, subnet, f"{kind}: {exc}")
        return "continue"

    async def _run_burst(self, account):
        targets = await self.repo.enabled_targets(account.id)
        if not targets:
            await self._state(scheduler_status="IDLE", next_cycle_at=None)
            return "stop"
        for number, subnet in enumerate(targets):
            current = await self.repo.get_account(account.id)
            if not current or current.scheduler_status != "RUNNING":
                return "stop"
            self.attempt_count += 1
            started = time.monotonic()
            await self._state(last_request_at=utcnow())
            try:
                async with self.lock:
                    client = await self.client_factory(account.id)
                    result = await client.create_floating_ip(subnet.region, subnet.subnet_id)
                await self._state(consecutive_network_errors=0)
            except SelectelError as exc:
                action = await self._error(current, subnet, started, exc)
                if action != "continue":
                    return action
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                action = await self._error(current, subnet, started, SelectelError(ErrorType.UNKNOWN, f"{type(exc).__name__}: {exc}"))
                if action != "continue":
                    return action
            else:
                await self._found(current, subnet, result, started)
                if current.stop_account_after_found:
                    await self._state(scheduler_status="PAUSED", next_cycle_at=None)
                    return "stop"
            if number + 1 < len(targets):
                scheduler_settings = await self.repo.scheduler_settings()
                delay = float(scheduler_settings.burst_request_delay or 1.0)
                if targets[number + 1].region != subnet.region:
                    delay += float(getattr(current, "region_delay", 1.0) or 1.0)
                await asyncio.sleep(max(0.0, delay))
        return "finished"

    async def run(self):
        if self.initial_delay:
            await asyncio.sleep(self.initial_delay)
        while True:
            account = await self.repo.get_account(self.account_id)
            if not account or not account.enabled or account.scheduler_status != "RUNNING":
                return
            if account.next_cycle_at and account.next_cycle_at > utcnow():
                await self._wait(account.next_cycle_at)
                continue
            if account.scheduler_status == "RATE_LIMIT_COOLDOWN":
                await self._state(scheduler_status="RUNNING", cooldown_until=None)
                await self._recalculate()
                account = await self.repo.get_account(self.account_id)
            self.cycle_id += 1
            cycle_started = utcnow()
            await self._state(last_cycle_started_at=cycle_started)
            result = await self._run_burst(account)
            if result == "stop":
                return
            if result == "cooldown":
                continue
            finished = utcnow()
            scheduler_settings = await self.repo.scheduler_settings()
            next_cycle = cycle_started + timedelta(seconds=max(0, int(scheduler_settings.burst_cooldown or 360)))
            await self._state(last_cycle_finished_at=finished, next_cycle_at=next_cycle)
            log.info("account=%s cycle=%s finished next_cycle=%s", account.id, self.cycle_id, next_cycle.isoformat())


AccountWorker = AccountBurstWorker
