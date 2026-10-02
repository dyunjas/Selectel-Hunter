from datetime import datetime, timezone
from sqlalchemy import and_, delete, func, select, update
from sqlalchemy.ext.asyncio import async_sessionmaker
from .models import Account, AccountSubnet, AccountTargetState, Attempt, FoundIP, HunterTask, NotificationSettings, RegionState, SchedulerSettings, TargetSubnet, TaskStatus
from app.config.regions import REGION_ORDER, REGIONS


def now():
    return datetime.now(timezone.utc).replace(tzinfo=None)


class Repository:
    def __init__(self, sessions: async_sessionmaker):
        self.sessions = sessions

    async def scheduler_settings(self):
        async with self.sessions() as s:
            settings = await s.get(SchedulerSettings, 1)
            if not settings:
                settings = SchedulerSettings(id=1)
                s.add(settings)
                await s.commit()
                await s.refresh(settings)
            return settings

    async def update_scheduler_settings(self, **values):
        async with self.sessions() as s:
            settings = await s.get(SchedulerSettings, 1)
            if not settings:
                settings = SchedulerSettings(id=1)
                s.add(settings)
            for key, value in values.items():
                if hasattr(settings, key):
                    setattr(settings, key, value)
            await s.commit()
            await s.refresh(settings)
            return settings

    async def add_account(self, **data) -> Account:
        async with self.sessions() as s:
            if data.get("region", "ru-3") not in REGIONS:
                raise ValueError("Неподдерживаемый регион аккаунта")
            count = await s.scalar(select(func.count(Account.id)).where(Account.enabled))
            if count >= 20:
                raise ValueError("Достигнут лимит: не более 20 аккаунтов")
            if "scheduler_position" not in data:
                position = await s.scalar(select(func.max(Account.scheduler_position)))
                data["scheduler_position"] = (position if position is not None else -1) + 1
            obj = Account(**data); s.add(obj); await s.commit(); await s.refresh(obj); return obj

    async def accounts(self, user_id: int):
        async with self.sessions() as s: return list((await s.scalars(select(Account).where(Account.telegram_user_id == user_id, Account.enabled))).all())

    async def notification_users(self):
        async with self.sessions() as s:
            rows = await s.execute(select(Account.telegram_user_id).where(Account.enabled).distinct())
            return [row[0] for row in rows.all()]

    async def all_accounts(self):
        async with self.sessions() as s: return list((await s.scalars(select(Account).where(Account.enabled))).all())

    async def get_account(self, account_id: int):
        async with self.sessions() as s: return await s.get(Account, account_id)

    async def update_account(self, account_id: int, **values):
        async with self.sessions() as s:
            await s.execute(update(Account).where(Account.id == account_id).values(**values)); await s.commit()

    async def apply_schedule(self, entries):
        """Persist a complete schedule atomically in UTC database time."""
        async with self.sessions() as s:
            for account_id, values in entries:
                await s.execute(update(Account).where(Account.id == account_id).values(**values))
            await s.commit()

    async def toggle_account_notification(self, account_id: int, field: str):
        allowed = {"notify_account_added", "notify_found", "notify_no_free_ip", "notify_permission", "notify_network", "notify_rate_limit", "notify_server", "notify_unknown", "notify_recovered", "notify_scheduler"}
        if field not in allowed: raise ValueError("Unknown notification setting")
        async with self.sessions() as s:
            account = await s.get(Account, account_id)
            if not account: return None
            setattr(account, field, not getattr(account, field)); await s.commit(); await s.refresh(account); return account

    async def toggle_account_notifications(self, account_id: int):
        async with self.sessions() as s:
            account = await s.get(Account, account_id)
            if not account:
                return None
            account.notifications_enabled = not account.notifications_enabled
            await s.commit(); await s.refresh(account)
            return account

    async def notification_settings(self):
        async with self.sessions() as s:
            settings = await s.get(NotificationSettings, 1)
            if not settings:
                settings = NotificationSettings(id=1)
                s.add(settings); await s.commit(); await s.refresh(settings)
            return settings

    async def update_notification_settings(self, **values):
        async with self.sessions() as s:
            settings = await s.get(NotificationSettings, 1)
            if not settings:
                settings = NotificationSettings(id=1); s.add(settings)
            for key, value in values.items():
                if hasattr(settings, key):
                    setattr(settings, key, value)
            await s.commit(); await s.refresh(settings)
            return settings

    async def delete_account(self, account_id: int):
        async with self.sessions() as s:
            await s.execute(delete(FoundIP).where(FoundIP.account_id == account_id))
            await s.execute(delete(HunterTask).where(HunterTask.account_id == account_id))
            await s.execute(delete(AccountSubnet).where(AccountSubnet.account_id == account_id))
            await s.execute(delete(AccountTargetState).where(AccountTargetState.account_id == account_id))
            await s.execute(delete(Attempt).where(Attempt.account_id == account_id))
            await s.execute(delete(Account).where(Account.id == account_id)); await s.commit()

    async def set_subnets(self, account_id: int, selected: list[dict]):
        if len(selected) > 6:
            raise ValueError("Для одного аккаунта можно выбрать не более 6 подсетей")
        async with self.sessions() as s:
            account = await s.get(Account, account_id)
            if not account:
                raise ValueError("Аккаунт не найден")
            await s.execute(update(AccountSubnet).where(AccountSubnet.account_id == account_id).values(enabled=False))
            for item in selected:
                item = {**item, "region": item.get("region", account.region)}
                existing = await s.scalar(select(AccountSubnet).where(and_(AccountSubnet.account_id == account_id, AccountSubnet.subnet_id == item["subnet_id"])))
                if existing: existing.enabled = True; existing.cidr = item["cidr"]; existing.region = item["region"]
                else: s.add(AccountSubnet(account_id=account_id, **item))
            await s.commit()

    async def enabled_subnets(self, account_id: int):
        async with self.sessions() as s:
            return list((await s.scalars(select(AccountSubnet).where(AccountSubnet.account_id == account_id, AccountSubnet.enabled))).all())

    async def enabled_targets(self, account_id: int | None = None):
        async with self.sessions() as s:
            query = select(TargetSubnet).join(RegionState, RegionState.region == TargetSubnet.region).where(TargetSubnet.enabled, RegionState.enabled).order_by(TargetSubnet.order_index, TargetSubnet.subnet_id)
            if account_id is not None:
                query = query.where(~select(AccountTargetState.id).where(AccountTargetState.account_id == account_id, AccountTargetState.subnet_id == TargetSubnet.subnet_id, ~AccountTargetState.enabled).exists())
            return list((await s.scalars(query)).all())

    async def all_targets(self):
        async with self.sessions() as s:
            return list((await s.scalars(select(TargetSubnet).order_by(TargetSubnet.order_index, TargetSubnet.subnet_id))).all())

    async def disable_account_target(self, account_id: int, subnet_id: str):
        async with self.sessions() as s:
            state = await s.scalar(select(AccountTargetState).where(AccountTargetState.account_id == account_id, AccountTargetState.subnet_id == subnet_id))
            if state:
                state.enabled = False
            else:
                s.add(AccountTargetState(account_id=account_id, subnet_id=subnet_id, enabled=False))
            await s.commit()

    async def set_target_enabled(self, subnet_id: str, enabled: bool):
        async with self.sessions() as s:
            await s.execute(update(TargetSubnet).where(TargetSubnet.subnet_id == subnet_id).values(enabled=enabled))
            await s.commit()

    async def set_targets_enabled(self, selected_ids: set[str]):
        async with self.sessions() as s:
            targets = list((await s.scalars(select(TargetSubnet))).all())
            for target in targets:
                target.enabled = target.subnet_id in selected_ids
            await s.commit()

    async def set_region_enabled(self, region: str, enabled: bool):
        async with self.sessions() as s:
            await s.execute(update(RegionState).where(RegionState.region == region).values(enabled=enabled))
            await s.commit()

    async def region_states(self):
        async with self.sessions() as s:
            states = list((await s.scalars(select(RegionState))).all())
            order = {region: index for index, region in enumerate(REGION_ORDER)}
            return sorted(states, key=lambda state: order.get(state.region, len(order)))

    async def disable_subnet(self, account_id: int, subnet_id: str):
        async with self.sessions() as s:
            await s.execute(update(AccountSubnet).where(AccountSubnet.account_id == account_id, AccountSubnet.subnet_id == subnet_id).values(enabled=False))
            await s.commit()

    async def record_attempt(self, **data):
        async with self.sessions() as s:
            obj = Attempt(**data)
            s.add(obj)
            await s.commit()
            await s.refresh(obj)
            return obj

    async def account_attempt_stats(self, account_id: int):
        async with self.sessions() as s:
            rows = await s.execute(select(Attempt.result, Attempt.subnet_id).where(Attempt.account_id == account_id))
            stats = {}
            for result, subnet_id in rows.all():
                key = str(result)
                stats[key] = stats.get(key, 0) + 1
            return stats

    async def account_statistics(self, account_id: int, since=None):
        async with self.sessions() as s:
            query = select(Attempt.result, func.count(Attempt.id), func.avg(Attempt.elapsed_ms)).where(Attempt.account_id == account_id)
            if since is not None:
                query = query.where(Attempt.attempted_at >= since)
            rows = await s.execute(query.group_by(Attempt.result))
            values = {str(result): {"count": count, "avg_ms": float(avg or 0)} for result, count, avg in rows.all()}
            total = sum(item["count"] for item in values.values())
            average = sum(item["avg_ms"] * item["count"] for item in values.values()) / total if total else 0
            return {"total": total, "average_ms": average, "by_result": values}

    async def user_attempt_stats(self, user_id: int):
        async with self.sessions() as s:
            rows = await s.execute(
                select(Attempt.result, func.count(Attempt.id))
                .join(Account, Account.id == Attempt.account_id)
                .where(Account.telegram_user_id == user_id)
                .group_by(Attempt.result)
            )
            return {str(result): count for result, count in rows.all()}

    async def create_task(self, **data):
        async with self.sessions() as s:
            duplicate = await s.scalar(select(HunterTask).where(HunterTask.account_id == data["account_id"], HunterTask.subnet_id == data["subnet_id"], HunterTask.status == TaskStatus.RUNNING.value))
            if duplicate: return duplicate
            obj = HunterTask(**data); s.add(obj); await s.commit(); await s.refresh(obj); return obj

    async def get_task(self, task_id: int):
        async with self.sessions() as s: return await s.get(HunterTask, task_id)

    async def running_tasks(self):
        async with self.sessions() as s: return list((await s.scalars(select(HunterTask).where(HunterTask.status == TaskStatus.RUNNING.value))).all())

    async def user_tasks(self, user_id: int):
        async with self.sessions() as s: return list((await s.scalars(select(HunterTask).where(HunterTask.telegram_user_id == user_id).order_by(HunterTask.id.desc()))).all())

    async def update_task(self, task_id: int, **values):
        async with self.sessions() as s: await s.execute(update(HunterTask).where(HunterTask.id == task_id).values(**values)); await s.commit()

    async def add_found(self, **data):
        async with self.sessions() as s: s.add(FoundIP(**data)); await s.commit()

    async def found(self, user_id: int):
        async with self.sessions() as s:
            return list((await s.scalars(select(FoundIP).join(HunterTask, FoundIP.hunter_task_id == HunterTask.id).where(HunterTask.telegram_user_id == user_id).order_by(FoundIP.id.desc()))).all())
