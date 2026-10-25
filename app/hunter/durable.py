"""Execute one durable target at a time within the existing BURST cadence."""
import asyncio
import json
import logging
import time
from ipaddress import ip_address, ip_network
from app.db.queue import LeaseLost
from app.selectel.errors import SelectelError

log = logging.getLogger(__name__)


class DurableBurst:
    def __init__(self, repo, client_factory):
        self.repo, self.queue, self.client_factory = repo, repo.queue, client_factory

    async def _heartbeat(self, job):
        while True:
            await asyncio.sleep(10)
            await self.queue.heartbeat(job)

    @staticmethod
    def marker(job):
        return f"selectel-hunter:{job.id}:{job.operation_id}"

    def owns(self, job, account, resource):
        return (bool(resource.get("id")) and resource.get("description") == self.marker(job)
                and (resource.get("project_id") or resource.get("tenant_id")) == account.project_id
                and resource.get("floating_network_id") == job.network_id)

    async def attempt(self, job, account, client):
        started = time.monotonic()
        outcome, http_status, retry_delay = "INTERRUPTED", None, None
        action = "reconcile" if job.operation_id else "allocate"
        try:
            if job.operation_id and job.resource_json:
                # A received response can be processed after a restart.
                self._log_request(job, action, "START", started)
                resource = json.loads(job.resource_json)
            else:
                if job.operation_id:
                    # The user prefers a fresh scheduled POST to reconciliation
                    # after a lost response. Keep the old operation audit row.
                    log.info("selectel_intent_replaced account=%s job=%s operation=%s",
                             job.account_id, job.id, job.operation_id)
                    await self.queue.change(job, operation_id=None, network_id=None)
                action = "allocate"
                network = await client.resolve_network(job.region, job.subnet_id)
                current = await self.queue.get(job.id)
                if current.status != "running":
                    await self.queue.release(job)
                    outcome = "SKIPPED_STATUS"
                    return
                await self.queue.begin_operation(job, network)
                self._log_request(job, action, "START", started)
                resource = await client.create_floating_ip(job.region, job.subnet_id, description=self.marker(job), network_id=network)
            if not self.owns(job, account, resource) or not resource.get("floating_ip_address"):
                retry_delay, next_attempt_at = await self._retry_schedule(account)
                await self.queue.release(job, next_attempt_at=next_attempt_at, error="Allocation ownership could not be verified; resource preserved")
                outcome = "RESOURCE_PRESERVED"
                return
            await self.queue.change(job, resource_json=json.dumps(resource))
            if ip_address(resource["floating_ip_address"]) in ip_network(job.cidr):
                await self.queue.complete(job, resource)
                outcome = "FOUND"
                log.info("selectel_found job=%s region=%s ip=%s floating_ip_id=%s", job.id, job.region,
                         resource["floating_ip_address"], resource["id"])
                return
            else:
                try:
                    fresh = await client.get_floating_ip(resource["id"], job.region)
                except SelectelError as exc:
                    if exc.status != 404:
                        raise
                    retry_delay, next_attempt_at = await self._retry_schedule(account)
                    await self.queue.release(job, next_attempt_at=next_attempt_at, clear_operation=True)
                    outcome = "CLEANUP_CONFIRMED"
                    return
                if not self.owns(job, account, fresh) or fresh.get("id") != resource["id"] or fresh.get("port_id"):
                    retry_delay, next_attempt_at = await self._retry_schedule(account)
                    await self.queue.release(job, next_attempt_at=next_attempt_at, error="Deletion ownership check failed; resource preserved", failed=True)
                    outcome = "DELETE_OWNERSHIP_FAILED"
                    return
                await self.queue.heartbeat(job)
                await client.delete_floating_ip(resource["id"], job.region)
                await self.queue.release(job, clear_operation=True)
                result = "REJECTED"
                outcome = result
            await self.repo.record_attempt(account_id=account.id, subnet_id=job.subnet_id, cidr=job.cidr,
                result=result, elapsed_ms=int((time.monotonic() - started) * 1000))
        except SelectelError as exc:
            outcome, http_status = exc.kind.value, exc.status
            # Without a received resource, the next normal slot creates a new IP.
            # Known resources retain their response for safe completion/cleanup.
            clear = not job.resource_json
            delay, next_attempt_at = await self._retry_schedule(account)
            retry_delay = delay
            await self.queue.release(job, delay=delay, next_attempt_at=next_attempt_at,
                error=f"{exc.kind.value}: HTTP {exc.status or '—'}", clear_operation=clear)
            await self.repo.record_attempt(account_id=account.id, subnet_id=job.subnet_id, cidr=job.cidr,
                result=exc.kind.value, http_status=exc.status, error_type=exc.kind.value)
        except LeaseLost:
            outcome = "LEASE_LOST"
            raise
        except asyncio.CancelledError:
            outcome = "CANCELLED"
            raise
        except Exception as exc:
            outcome = "WORKER_ERROR"
            retry_delay, next_attempt_at = await self._retry_schedule(account)
            log.warning("selectel_worker_error job=%s region=%s error_type=%s", job.id, job.region, type(exc).__name__)
            await self.queue.release(job, next_attempt_at=next_attempt_at, error=f"Worker error: {type(exc).__name__}", clear_operation=not job.resource_json)
        finally:
            self._log_request(job, action, outcome, started, http_status, retry_delay)

    @staticmethod
    def _log_request(job, action, result, started, http_status=None, retry_delay=None):
        log.info(
            "selectel_request account=%s job=%s region=%s subnet=%s subnet_id=%s attempt=%s "
            "action=%s result=%s http=%s elapsed_ms=%s retry_seconds=%s",
            job.account_id, job.id, job.region, job.cidr, job.subnet_id, job.attempts,
            action, result, http_status if http_status is not None else "—",
            int((time.monotonic() - started) * 1000),
            round(retry_delay, 2) if retry_delay is not None else "—",
        )

    async def _retry_schedule(self, account):
        from app.db.queue import now
        from .account_worker import next_scheduled_cycle
        at = now()
        settings = await self.repo.scheduler_settings()
        period = max(1, int(settings.burst_cooldown or 360))
        planned = account.next_cycle_at or account.last_cycle_started_at or at
        next_attempt = next_scheduled_cycle(planned, at, period)
        # Errors, their history and Retry-After never extend the BURST cadence.
        return max(0, (next_attempt - at).total_seconds()), next_attempt

    async def run(self, account):
        client = await self.client_factory(account.id)
        seen = []
        allocated, reconciled = 0, 0
        started = time.monotonic()
        try:
            while True:
                current = await self.repo.get_account(account.id)
                if not current or current.scheduler_status != "RUNNING":
                    return "stop"
                job = await self.queue.claim(account.id, seen)
                if job is None:
                    return "finished"
                seen.append(job.id)
                enabled = {t.subnet_id for t in await self.repo.enabled_targets(account.id)}
                if job.subnet_id not in enabled:
                    await self.queue.release(job, delay=60)
                    continue
                heartbeat = asyncio.create_task(self._heartbeat(job))
                previous_attempts = job.attempts
                reconciled += int(bool(job.operation_id))
                attempt = asyncio.create_task(self.attempt(job, current, client))
                try:
                    done, _ = await asyncio.wait({heartbeat, attempt}, timeout=180, return_when=asyncio.FIRST_COMPLETED)
                    if not done:
                        raise TimeoutError("Durable job exceeded 180 seconds")
                    for task in done:
                        task.result()
                finally:
                    heartbeat.cancel()
                    attempt.cancel()
                    await asyncio.gather(heartbeat, attempt, return_exceptions=True)
                    allocated += job.attempts - previous_attempts
                saved = await self.queue.get(job.id)
                if saved.status == "completed" and current.stop_account_after_found:
                    await self.repo.update_account(account.id, scheduler_status="PAUSED", next_cycle_at=None)
                    return "stop"
                settings = await self.repo.scheduler_settings()
                await asyncio.sleep(max(0.3, settings.burst_request_delay))
        finally:
            await client.close()
            jobs = await self.queue.list(account_id=account.id)
            log.info(
                "selectel_burst account=%s allocation_attempts=%s reconciliations=%s "
                "pending=%s unresolved=%s failed=%s paused=%s completed=%s elapsed_ms=%s",
                account.id, allocated, reconciled,
                sum(j.status == "pending" for j in jobs),
                sum(j.status in {"pending", "running"} and bool(j.operation_id) for j in jobs),
                sum(j.status == "failed" for j in jobs),
                sum(j.status in {"paused", "cancelled"} for j in jobs),
                sum(j.status == "completed" for j in jobs),
                int((time.monotonic() - started) * 1000),
            )
