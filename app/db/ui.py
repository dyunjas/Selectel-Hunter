import os
import json
import uuid
from datetime import timedelta
from sqlalchemy import select, func, update, delete, text
from sqlalchemy.dialects.sqlite import insert
from .models import UserPreferences, JobUiState, JobOperation, QueueJob, Account, FoundIP, Attempt, TargetSubnet, ResourceObservation
from app.bot.presentation import regions_for, utcnow
from .models import SubnetDraft, NotificationCooldown


class UiRepository:
    def __init__(self, sessions):
        self.sessions = sessions

    async def notification_cooldown(self, key, seconds, mark=False):
        async with self.sessions() as s:
            if mark:
                await s.execute(insert(NotificationCooldown).values(key=key, sent_at=utcnow()).on_conflict_do_update(
                    index_elements=[NotificationCooldown.key], set_={"sent_at": utcnow()}))
                await s.commit()
                return False
            previous = await s.get(NotificationCooldown, key)
            return previous is not None and (utcnow() - previous.sent_at).total_seconds() < seconds

    async def prune(self):
        async with self.sessions() as s:
            await s.execute(delete(NotificationCooldown).where(NotificationCooldown.sent_at < utcnow() - timedelta(days=7)))
            await s.execute(delete(SubnetDraft).where(SubnetDraft.created_at < utcnow() - timedelta(days=30)))
            await s.commit()

    async def draft(self, draft_id, user_id, selected=None, toggle=None):
        async with self.sessions() as s:
            if selected is not None or toggle is not None:
                await s.execute(text("BEGIN IMMEDIATE"))
            row = await s.get(SubnetDraft, draft_id)
            if not row or row.telegram_user_id != user_id:
                return None
            if toggle is not None:
                selected = set(json.loads(row.selected_json))
                selected.symmetric_difference_update({toggle})
            if selected is not None:
                row.selected_json = json.dumps(sorted(selected))
                await s.commit()
            return row

    async def create_draft(self, user_id, account_id, selection, selected):
        async with self.sessions() as s:
            ids = list((await s.scalars(select(SubnetDraft.id).where(SubnetDraft.telegram_user_id == user_id).order_by(SubnetDraft.created_at.desc()))).all())
            if len(ids) >= 20:
                await s.execute(delete(SubnetDraft).where(SubnetDraft.id.in_(ids[19:])))
            draft = SubnetDraft(id=uuid.uuid4().hex[:12], telegram_user_id=user_id, account_id=account_id,
                selection=selection, selected_json=json.dumps(sorted(selected)), created_at=utcnow())
            s.add(draft)
            await s.commit()
            return draft

    async def preferences(self, user_id, **values):
        if "selected_region" in values and not regions_for(values["selected_region"]):
            raise ValueError("Invalid region selection")
        if "utc_offset_minutes" in values and not -720 <= values["utc_offset_minutes"] <= 840:
            raise ValueError("Invalid UTC offset")
        if "refresh_seconds" in values and not 15 <= values["refresh_seconds"] <= 300:
            raise ValueError("Refresh interval must be 15–300 seconds")
        async with self.sessions() as s:
            existing = await s.get(UserPreferences, user_id)
            if existing is None:
                await s.execute(insert(UserPreferences).values(telegram_user_id=user_id, selected_region="spb", utc_offset_minutes=300,
                    refresh_seconds=max(15, min(300, int(os.getenv("UI_REFRESH_SECONDS", "20"))))).on_conflict_do_nothing())
            if values:
                await s.execute(update(UserPreferences).where(UserPreferences.telegram_user_id == user_id).values(**values))
            await s.commit()
            return await s.get(UserPreferences, user_id)

    async def job_state(self, job_id):
        async with self.sessions() as s:
            return await s.get(JobUiState, job_id)

    async def position(self, job):
        # Sequential order within one account; other accounts run independently.
        async with self.sessions() as s:
            ids = list((await s.scalars(select(QueueJob.id).where(QueueJob.account_id == job.account_id,
                QueueJob.status.in_(["pending", "running"])).order_by(QueueJob.due_at, QueueJob.created_at, QueueJob.id))).all())
            return ids.index(job.id) + 1 if job.id in ids else None

    async def history(self, user_id, region="all", job_id=None, page=0, size=5):
        async with self.sessions() as s:
            query = select(FoundIP).join(Account, Account.id == FoundIP.account_id).where(Account.telegram_user_id == user_id, FoundIP.region.in_(regions_for(region)))
            if job_id:
                query = query.join(QueueJob, (QueueJob.account_id == FoundIP.account_id) & (QueueJob.region == FoundIP.region) & (QueueJob.found_id == FoundIP.floating_ip_id)).where(QueueJob.id == job_id)
            count = await s.scalar(select(func.count()).select_from(query.subquery()))
            pages = max(1, (count + size - 1) // size)
            page = max(0, min(page, pages - 1))
            rows = list((await s.scalars(query.order_by(FoundIP.found_at.desc(), FoundIP.id.desc()).offset(page * size).limit(size))).all())
            return rows, page, pages, count

    async def found_item(self, user_id, found_id=None, task_id=None):
        async with self.sessions() as s:
            query = select(FoundIP).join(Account, Account.id == FoundIP.account_id).where(Account.telegram_user_id == user_id)
            query = query.where(FoundIP.id == found_id) if found_id is not None else query.where(FoundIP.hunter_task_id == task_id)
            return await s.scalar(query)

    async def found_job(self, item):
        async with self.sessions() as s:
            return await s.scalar(select(QueueJob).where(QueueJob.account_id == item.account_id, QueueJob.region == item.region, QueueJob.found_id == item.floating_ip_id))

    async def observation(self, found_id, status=None):
        async with self.sessions() as s:
            if status is not None:
                await s.execute(insert(ResourceObservation).values(found_id=found_id, status=status, checked_at=utcnow()).on_conflict_do_update(
                    index_elements=[ResourceObservation.found_id], set_={"status": status, "checked_at": utcnow()}))
                await s.commit()
            return await s.get(ResourceObservation, found_id)

    async def stats(self, user_id, region="all"):
        at = utcnow()
        regions = regions_for(region)
        async with self.sessions() as s:
            jobs = select(QueueJob).where(QueueJob.telegram_user_id == user_id, QueueJob.region.in_(regions)).subquery()
            totals = (await s.execute(select(func.count(), func.coalesce(func.sum(jobs.c.attempts), 0)).select_from(jobs))).one()
            active = await s.scalar(select(func.count()).select_from(QueueJob).join(Account).where(QueueJob.telegram_user_id == user_id,
                QueueJob.region.in_(regions), QueueJob.status.in_(["running", "pending"]), Account.enabled,
                Account.scheduler_status.in_(["RUNNING", "RATE_LIMIT_COOLDOWN"])))
            finds = select(FoundIP).join(Account).where(Account.telegram_user_id == user_id, FoundIP.region.in_(regions)).subquery()
            found = await s.scalar(select(func.count()).select_from(finds))
            found_day = await s.scalar(select(func.count()).select_from(finds).where(finds.c.found_at >= at - timedelta(days=1)))
            operations = select(JobOperation.started_at).join(QueueJob).where(QueueJob.telegram_user_id == user_id, QueueJob.region.in_(regions)).subquery()
            hour = await s.scalar(select(func.count()).select_from(operations).where(operations.c.started_at >= at - timedelta(hours=1)))
            day = await s.scalar(select(func.count()).select_from(operations).where(operations.c.started_at >= at - timedelta(days=1)))
            first = await s.scalar(select(func.min(operations.c.started_at)))
            states = list((await s.scalars(select(JobUiState).join(QueueJob).where(QueueJob.telegram_user_id == user_id, QueueJob.region.in_(regions)))).all())
            elapsed = sum(st.active_seconds + (max(0, (at - st.active_since).total_seconds()) if st.active_since else 0) for st in states)
            complete_timing = len(states) == totals[0] and all(st.timing_complete for st in states)
            success = max((st.last_success_at for st in states if st.last_success_at), default=None)
            error_query = select(func.count(Attempt.id)).join(Account).outerjoin(TargetSubnet, TargetSubnet.subnet_id == Attempt.subnet_id).where(
                Account.telegram_user_id == user_id, Attempt.result.not_in(["FOUND", "NO_FREE_IP", "REJECTED"]))
            if region != "all":
                error_query = error_query.where(TargetSubnet.region.in_(regions))
            errors = await s.scalar(error_query)
            return {"jobs": totals[0], "attempts": totals[1], "active": active, "found": found, "found_day": found_day,
                    "hour": hour if first else None, "day": day if first else None,
                    "rate": day / max(1, min(86400, (at - first).total_seconds())) * 60 if first else None,
                    "errors": errors, "elapsed": elapsed if complete_timing and states else None, "success": success}
