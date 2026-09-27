import asyncio, logging, random, time
from datetime import datetime, timezone
from app.selectel.errors import ErrorType, SelectelError
from app.db.models import TaskStatus

log = logging.getLogger(__name__)


class HunterWorker:
    def __init__(self, task, repository, client, notify): self.task, self.repo, self.client, self.notify, self.lock = task, repository, client, notify, asyncio.Lock()

    async def run(self):
        started = time.monotonic(); await self.repo.update_task(self.task.id, started_at=datetime.now(timezone.utc).replace(tzinfo=None))
        try:
            while True:
                current = await self.repo.get_task(self.task.id)
                if not current or current.status != TaskStatus.RUNNING.value: return
                async with self.lock:
                    current = await self.repo.get_task(self.task.id)
                    if not current or current.status != TaskStatus.RUNNING.value: return
                    await self.repo.update_task(current.id, attempts=current.attempts + 1, last_attempt_at=datetime.now(timezone.utc).replace(tzinfo=None))
                    try:
                        result = await self.client.create_floating_ip(current.subnet_id)
                    except SelectelError as exc:
                        await self.repo.update_task(current.id, last_error=exc.kind.value)
                        try: await self.notify(current, None, None, 0, exc.kind.value)
                        except Exception: log.exception("notification failed", extra={"task_id": current.id})
                        if exc.kind in (ErrorType.NO_FREE_IP, ErrorType.RATE_LIMIT, ErrorType.NETWORK_ERROR, ErrorType.SERVER_ERROR):
                            delay = exc.retry_after or random.uniform(current.min_interval, current.max_interval)
                            await asyncio.sleep(max(3, min(delay, 300))); continue
                        await self.repo.update_task(current.id, status=TaskStatus.ERROR.value, finished_at=datetime.now(timezone.utc).replace(tzinfo=None)); return
                    ip = result.get("floating_ip_address"); fip_id = result.get("id")
                    elapsed = time.monotonic() - started
                    await self.repo.update_task(current.id, status=TaskStatus.FOUND.value, floating_ip_address=ip, floating_ip_id=fip_id, elapsed_seconds=elapsed, finished_at=datetime.now(timezone.utc).replace(tzinfo=None), last_error=None)
                    await self.repo.add_found(account_id=current.account_id, hunter_task_id=current.id, subnet_id=current.subnet_id, subnet_cidr=current.subnet_cidr, floating_ip_id=fip_id or "", floating_ip_address=ip or "", attempts=current.attempts + 1)
                    try: await self.notify(current, ip, fip_id, elapsed, "FOUND")
                    except Exception: log.exception("found notification failed", extra={"task_id": current.id})
                    return
        except asyncio.CancelledError:
            raise
        except Exception:
            log.exception("worker iteration failed", extra={"task_id": self.task.id})
            await self.repo.update_task(self.task.id, last_error="UNKNOWN")
            await asyncio.sleep(10)
