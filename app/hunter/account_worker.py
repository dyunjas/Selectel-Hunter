import asyncio
import logging
import random
import time
from datetime import datetime, timedelta
from types import SimpleNamespace

from app.db.models import TaskStatus
from app.selectel.errors import ErrorType, SelectelError

log = logging.getLogger(__name__)


def utcnow():
    return datetime.utcnow()


class AccountWorker:
    """One sequential scheduler for one account.

    The worker owns the round-robin cursor.  A request is made only while its
    account lock is held, so a duplicate scheduler can never create a burst.
    """

    NETWORK_DELAYS = (30, 60, 120, 300)
    RATE_DELAYS = (300, 600, 1200, 1800)

    def __init__(self, account_id, repo, client_factory, notify, lock=None, initial_delay=0):
        self.account_id = account_id
        self.repo = repo
        self.client_factory = client_factory
        self.notify = notify
        self.lock = lock or asyncio.Lock()
        self.initial_delay = max(0.0, initial_delay)
        self.attempt_count = 0

    @staticmethod
    def _interval(account):
        minimum = max(30, int(account.min_interval or 30))
        maximum = max(minimum, int(account.max_interval or 60))
        return random.uniform(minimum, maximum)

    async def _state(self, **values):
        await self.repo.update_account(self.account_id, **values)

    async def _wait_until(self, when):
        if when:
            await asyncio.sleep(max(0.0, (when - utcnow()).total_seconds()))

    async def _notify(self, account, subnet, event, ip=None, fip_id=None, elapsed=0):
        subject = SimpleNamespace(
            account_id=account.id,
            telegram_user_id=account.telegram_user_id,
            subnet_cidr=subnet.cidr,
            subnet_id=subnet.subnet_id,
            attempts=self.attempt_count,
        )
        try:
            await self.notify(subject, ip, fip_id, elapsed, event)
        except Exception:
            log.exception("scheduler notification failed", extra={"account_id": self.account_id})

    async def _record(self, subnet, result, started, exc=None):
        await self.repo.record_attempt(
            account_id=self.account_id,
            subnet_id=subnet.subnet_id,
            cidr=subnet.cidr,
            result=result,
            http_status=getattr(exc, "status", None),
            error_type=getattr(getattr(exc, "kind", None), "value", None),
            elapsed_ms=int((time.monotonic() - started) * 1000),
        )

    async def _normal_schedule(self):
        next_at = utcnow() + timedelta(seconds=self._interval(await self.repo.get_account(self.account_id)))
        await self._state(next_request_at=next_at)

    async def _advance(self, subnets, current_index):
        if not subnets:
            await self._state(scheduler_status="IDLE", next_request_at=None)
            return
        await self._state(current_subnet_index=(current_index + 1) % len(subnets))

    async def _found(self, account, subnet, result, started):
        ip = result.get("floating_ip_address")
        fip_id = result.get("id")
        elapsed = time.monotonic() - started
        await self._record(subnet, "FOUND", started)
        task = await self.repo.create_task(
            telegram_user_id=account.telegram_user_id,
            account_id=account.id,
            subnet_id=subnet.subnet_id,
            subnet_cidr=subnet.cidr,
            network_id="",
            region=account.region,
            status=TaskStatus.FOUND.value,
            attempts=self.attempt_count,
            min_interval=max(30, account.min_interval),
            max_interval=max(max(30, account.min_interval), account.max_interval),
            floating_ip_address=ip,
            floating_ip_id=fip_id,
            elapsed_seconds=elapsed,
            finished_at=utcnow(),
        )
        await self.repo.add_found(
            account_id=account.id, hunter_task_id=task.id, subnet_id=subnet.subnet_id,
            subnet_cidr=subnet.cidr, region=account.region, floating_ip_id=fip_id or "", floating_ip_address=ip or "",
            attempts=self.attempt_count,
        )
        await self.repo.disable_subnet(account.id, subnet.subnet_id)
        await self._notify(account, subnet, "FOUND", ip, fip_id, elapsed)

    async def _handle_error(self, account, subnet, index, started, exc):
        kind = exc.kind.value
        await self._record(subnet, kind, started, exc)
        now = utcnow()
        if kind == ErrorType.NO_FREE_IP.value:
            await self._state(consecutive_network_errors=0)
            await self._advance(await self.repo.enabled_subnets(account.id), index)
            await self._normal_schedule()
            await self._notify(account, subnet, f"NO_FREE_IP: {exc}")
            return True
        if kind in (ErrorType.PERMISSION_ERROR.value, ErrorType.AUTH_ERROR.value):
            await self._state(scheduler_status="BLOCKED", next_request_at=None, last_request_at=now)
            await self._notify(account, subnet, f"{kind}: HTTP {exc.status or '—'}: {exc}")
            return False
        if kind == ErrorType.RATE_LIMIT.value:
            retry = exc.retry_after or self.RATE_DELAYS[min(max(0, self.attempt_count - 1), len(self.RATE_DELAYS) - 1)]
            entering = not account.cooldown_until or account.cooldown_until <= now
            until = now + timedelta(seconds=float(retry))
            await self._state(cooldown_until=until, next_request_at=until, scheduler_status="RUNNING")
            if entering:
                await self._notify(account, subnet, f"RATE_LIMIT: HTTP 429: cooldown {int(retry)} сек. — {exc}")
            return True
        if kind == ErrorType.NETWORK_ERROR.value:
            count = int(account.consecutive_network_errors or 0) + 1
            if count >= 5:
                until = now + timedelta(minutes=10)
                await self._state(consecutive_network_errors=count, scheduler_status="NETWORK_COOLDOWN", cooldown_until=until, next_request_at=until)
            else:
                delay = self.NETWORK_DELAYS[min(count - 1, len(self.NETWORK_DELAYS) - 1)]
                until = now + timedelta(seconds=delay)
                await self._state(consecutive_network_errors=count, next_request_at=until)
            log.warning("network retry scheduled account_id=%s attempt=%s", self.account_id, self.attempt_count)
            return True
        await self._advance(await self.repo.enabled_subnets(account.id), index)
        await self._normal_schedule()
        await self._notify(account, subnet, f"{kind}: {exc}")
        return True

    async def run(self):
        if self.initial_delay:
            await asyncio.sleep(self.initial_delay)
        while True:
            account = await self.repo.get_account(self.account_id)
            if not account or not account.enabled or account.scheduler_status != "RUNNING":
                return
            subnets = await self.repo.enabled_subnets(account.id)
            if not subnets:
                await self._state(scheduler_status="IDLE", next_request_at=None)
                return
            index = int(account.current_subnet_index or 0) % len(subnets)
            cooldown = account.cooldown_until if account.cooldown_until and account.cooldown_until > utcnow() else None
            target = cooldown or account.next_request_at
            if target and target > utcnow():
                await self._wait_until(target)
                continue
            subnet = subnets[index]
            started = time.monotonic()
            self.attempt_count += 1
            await self._state(last_request_at=utcnow())
            try:
                async with self.lock:
                    client = await self.client_factory(account.id)
                    result = await client.create_floating_ip(subnet.subnet_id)
            except SelectelError as exc:
                if exc.kind != ErrorType.NETWORK_ERROR:
                    await self._state(consecutive_network_errors=0)
                if not await self._handle_error(account, subnet, index, started, exc):
                    return
                continue
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                wrapped = SelectelError(ErrorType.UNKNOWN, f"{type(exc).__name__}: {exc}")
                if not await self._handle_error(account, subnet, index, started, wrapped):
                    return
                continue
            await self._state(consecutive_network_errors=0, cooldown_until=None)
            await self._found(account, subnet, result, started)
            remaining = await self.repo.enabled_subnets(account.id)
            if not remaining:
                await self._state(scheduler_status="IDLE", next_request_at=None)
                return
            # The removed subnet shifts the next target into the same index.
            await self._state(current_subnet_index=index % len(remaining))
            await self._normal_schedule()
