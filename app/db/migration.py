"""One-time import: old rows and settings remain intact for audit/rollback."""
from sqlalchemy import text
from app.db.models import TaskStatus


async def migrate_queue(repo):
    async with repo.sessions() as s:
        await s.execute(text("CREATE TABLE IF NOT EXISTS queue_migrations (version INTEGER PRIMARY KEY)"))
        await s.commit()
        if await s.scalar(text("SELECT version FROM queue_migrations WHERE version=1")):
            return
    # Enqueue is idempotent; an interrupted import can be replayed safely.
    for account in await repo.all_accounts():
        legacy = [task for task in await repo.user_tasks(account.telegram_user_id)
                  if task.account_id == account.id and task.status in {"RUNNING", "PAUSED", "ERROR"}]
        if legacy:
            for task in legacy:
                await repo.queue.enqueue(account, [{"region": task.region, "subnet_id": task.subnet_id, "cidr": task.subnet_cidr}])
                jobs = await repo.queue.list(account_id=account.id)
                job = next(j for j in jobs if j.subnet_id == task.subnet_id and j.region == task.region)
                async with repo.sessions() as s:
                    # Pre-upgrade RUNNING tasks may have lost their last POST response.
                    # Preserve them, but require review before issuing any new allocation.
                    await s.execute(text("UPDATE queue_jobs SET status=:status, attempts=:attempts, last_error=:error WHERE id=:id"),
                        {"id": job.id, "status": "paused", "attempts": task.attempts,
                         "error": "Imported legacy task: check existing Selectel IPs before resuming"})
                    await s.commit()
        elif account.scheduler_status in {"RUNNING", "PAUSED", "RATE_LIMIT_COOLDOWN"} or account.auto_start:
            targets = await repo.enabled_targets(account.id)
            # Moscow is new and must never become an implicit legacy target.
            await repo.queue.enqueue(account, [{"region": t.region, "subnet_id": t.subnet_id, "cidr": t.cidr} for t in targets if t.region != "ru-2"])
            if account.scheduler_status == "PAUSED" or (account.last_cycle_started_at and
                    (not account.last_cycle_finished_at or account.last_cycle_started_at > account.last_cycle_finished_at)):
                for job in await repo.queue.list(account_id=account.id):
                    await repo.queue.control(job.id, account.telegram_user_id, "paused")
    async with repo.sessions() as s:
        await s.execute(text("INSERT OR IGNORE INTO queue_migrations(version) VALUES (1)"))
        await s.commit()
