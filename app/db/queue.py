"""Durable target jobs. All claims and mutations are short SQLite transactions."""
import json
import uuid
from datetime import datetime, timedelta, timezone
from sqlalchemy import select, update, text
from .models import QueueJob, NotificationOutbox, HunterTask, FoundIP, Attempt
from .models import JobOperation
from .ui_events import transition, ui_state, event, problem, healthy


def now():
    return datetime.now(timezone.utc).replace(tzinfo=None)


class LeaseLost(RuntimeError):
    pass


class DurableQueue:
    def __init__(self, sessions):
        self.sessions = sessions

    async def enqueue(self, account, targets, exclusive=False):
        if not targets:
            if exclusive:
                raise ValueError("Выберите хотя бы одну доступную подсеть")
            return
        selected = {(target["region"], target["subnet_id"]) for target in targets}
        async with self.sessions() as s:
            await s.execute(text("BEGIN IMMEDIATE"))
            if exclusive:
                existing = list((await s.scalars(select(QueueJob).where(QueueJob.account_id == account.id,
                    QueueJob.telegram_user_id == account.telegram_user_id, QueueJob.status.in_(["pending", "running"])))).all())
                for job in existing:
                    if (job.region, job.subnet_id) not in selected:
                        previous = job.status
                        job.status, job.updated_at = "paused", now()
                        # Keep lease and operation intent: an in-flight POST may still finish.
                        await transition(s, job, previous, now())
            for target in targets:
                region, subnet_id, cidr = target["region"], target["subnet_id"], target["cidr"]
                job = await s.scalar(select(QueueJob).where(QueueJob.account_id == account.id, QueueJob.region == region, QueueJob.subnet_id == subnet_id))
                if not job:
                    job = QueueJob(id=uuid.uuid4().hex, account_id=account.id, telegram_user_id=account.telegram_user_id, region=region, subnet_id=subnet_id, cidr=cidr, status="pending", due_at=now(), created_at=now(), updated_at=now())
                    s.add(job)
                    await s.flush()
                    await transition(s, job, None, now(), new=True)
                elif job.status in {"paused", "cancelled", "failed"} and not job.found_id:
                    previous = job.status
                    job.status, job.due_at, job.updated_at = "pending", now(), now()
                    await transition(s, job, previous, now())
            await s.commit()

    async def list(self, user_id=None, account_id=None):
        async with self.sessions() as s:
            query = select(QueueJob).order_by(QueueJob.created_at, QueueJob.id)
            if user_id is not None:
                query = query.where(QueueJob.telegram_user_id == user_id)
            if account_id is not None:
                query = query.where(QueueJob.account_id == account_id)
            return list((await s.scalars(query)).all())

    async def get(self, job_id):
        async with self.sessions() as s:
            return await s.get(QueueJob, job_id)

    async def control(self, job_id, user_id, status):
        if status not in {"paused", "pending", "cancelled"}:
            raise ValueError("Invalid job transition")
        async with self.sessions() as s:
            await s.execute(text("BEGIN IMMEDIATE"))
            job = await s.get(QueueJob, job_id)
            if not job or job.telegram_user_id != user_id or job.status == "completed":
                return False
            previous = job.status
            job.status, job.updated_at, job.due_at = status, now(), now()
            await transition(s, job, previous, now())
            await s.commit()
            return True

    async def recover(self, at=None):
        at = at or now()
        async with self.sessions() as s:
            result = await s.execute(update(QueueJob).where(QueueJob.lease_until <= at).values(
                lease_token=None, lease_until=None,
                # A user pause/cancellation must survive recovery.
                status=text("CASE WHEN status = 'running' THEN 'pending' ELSE status END"), updated_at=at))
            await s.commit()
            return result.rowcount

    async def claim(self, account_id, excluded=(), lease_seconds=60):
        at = now()
        async with self.sessions() as s:
            await s.execute(text("BEGIN IMMEDIATE"))
            job = await s.scalar(select(QueueJob).where(
                QueueJob.account_id == account_id, QueueJob.status == "pending",
                QueueJob.due_at <= at, QueueJob.lease_token.is_(None),
                QueueJob.id.not_in(excluded),
            ).order_by(QueueJob.due_at, QueueJob.created_at).limit(1))
            if not job:
                return None
            job.status, job.lease_token = "running", uuid.uuid4().hex
            job.lease_until, job.updated_at = at + timedelta(seconds=lease_seconds), at
            await s.commit()
            return job

    async def change(self, job, require_running=False, **values):
        async with self.sessions() as s:
            query = update(QueueJob).where(QueueJob.id == job.id, QueueJob.lease_token == job.lease_token, QueueJob.lease_until > now())
            if require_running:
                query = query.where(QueueJob.status == "running")
            result = await s.execute(query.values(updated_at=now(), **values))
            if not result.rowcount:
                raise LeaseLost(job.id)
            if "attempts" in values:
                state = await ui_state(s, job)
                state.last_attempt_at = now()
                s.add(JobOperation(operation_id=values["operation_id"], job_id=job.id, started_at=state.last_attempt_at))
            await s.commit()
        for key, value in values.items():
            setattr(job, key, value)

    async def heartbeat(self, job):
        await self.change(job, lease_until=now() + timedelta(seconds=60))

    async def begin_operation(self, job, network_id):
        await self.change(job, require_running=True, operation_id=uuid.uuid4().hex, network_id=network_id, attempts=job.attempts + 1)

    async def release(self, job, delay=30, error=None, clear_operation=False, failed=False, next_attempt_at=None):
        async with self.sessions() as s:
            await s.execute(text("BEGIN IMMEDIATE"))
            current = await s.get(QueueJob, job.id)
            if current.lease_token != job.lease_token or not current.lease_until or current.lease_until <= now():
                raise LeaseLost(job.id)
            if current.status == "running":
                current.status = "failed" if failed else "pending"
            if failed:
                from .ui_events import stop_clock
                state = await ui_state(s, current)
                stop_clock(state, now())
                state.paused_at, state.pause_reason = now(), "error"
            current.lease_token = current.lease_until = None
            current.due_at, current.updated_at = next_attempt_at or now() + timedelta(seconds=delay), now()
            if error:
                current.errors += 1
                current.last_error = error[:1000]
                await problem(s, current, error, now())
            elif clear_operation:
                await healthy(s, current, now())
            if clear_operation:
                current.operation_id = current.resource_json = current.network_id = None
            await s.commit()

    async def complete(self, job, resource):
        async with self.sessions() as s:
            await s.execute(text("BEGIN IMMEDIATE"))
            current = await s.get(QueueJob, job.id)
            if current.lease_token != job.lease_token or not current.lease_until or current.lease_until <= now():
                raise LeaseLost(job.id)
            ip, fip_id = resource["floating_ip_address"], resource["id"]
            task = HunterTask(account_id=job.account_id, telegram_user_id=job.telegram_user_id, subnet_id=job.subnet_id,
                subnet_cidr=job.cidr, network_id=job.network_id or "", region=job.region, status="FOUND", attempts=job.attempts,
                floating_ip_id=fip_id, floating_ip_address=ip, finished_at=now())
            s.add(task)
            await s.flush()
            s.add(FoundIP(account_id=job.account_id, hunter_task_id=task.id, subnet_id=job.subnet_id,
                subnet_cidr=job.cidr, region=job.region, floating_ip_id=fip_id, floating_ip_address=ip, attempts=job.attempts))
            s.add(NotificationOutbox(job_id=job.id, payload=json.dumps({"task_id": task.id, "ip": ip, "fip_id": fip_id})))
            s.add(Attempt(account_id=job.account_id, subnet_id=job.subnet_id, cidr=job.cidr, result="FOUND"))
            current.status, current.found_ip, current.found_id = "completed", ip, fip_id
            current.lease_token = current.lease_until = None
            current.operation_id = current.resource_json = None
            current.last_error, current.updated_at = None, now()
            from .ui_events import stop_clock
            await healthy(s, current, now())
            stop_clock(await ui_state(s, current), now())
            await s.commit()

    async def pending_notifications(self):
        async with self.sessions() as s:
            return list((await s.scalars(select(NotificationOutbox).where(NotificationOutbox.delivered_at.is_(None), NotificationOutbox.due_at <= now()).order_by(text("CASE WHEN event_type = 'FOUND' THEN 0 ELSE 1 END"), NotificationOutbox.id).limit(20))).all())

    async def notification_result(self, item, delivered, delay=30):
        async with self.sessions() as s:
            await s.execute(update(NotificationOutbox).where(NotificationOutbox.id == item.id, NotificationOutbox.payload == item.payload).values(
                delivered_at=now() if delivered else None, attempts=item.attempts + 1, due_at=now() + timedelta(seconds=delay)))
            await s.commit()

    async def announce_restore(self):
        async with self.sessions() as s:
            await s.execute(text("BEGIN IMMEDIATE"))
            jobs = list((await s.scalars(select(QueueJob).where(QueueJob.status.in_(["pending", "running"]), QueueJob.attempts > 0))).all())
            for job in jobs:
                await event(s, job, await ui_state(s, job), "RESTORED", now())
            await s.commit()
