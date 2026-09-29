from datetime import datetime, timezone
from sqlalchemy import and_, delete, func, select, update
from sqlalchemy.ext.asyncio import async_sessionmaker
from .models import Account, AccountSubnet, Attempt, FoundIP, HunterTask, TaskStatus
from app.config.regions import REGIONS


def now():
    return datetime.now(timezone.utc).replace(tzinfo=None)


class Repository:
    def __init__(self, sessions: async_sessionmaker):
        self.sessions = sessions

    async def add_account(self, **data) -> Account:
        async with self.sessions() as s:
            if data.get("region", "ru-3") not in REGIONS:
                raise ValueError("Неподдерживаемый регион аккаунта")
            count = await s.scalar(select(func.count(Account.id)).where(Account.enabled))
            if count >= 20:
                raise ValueError("Достигнут лимит: не более 20 аккаунтов")
            obj = Account(**data); s.add(obj); await s.commit(); await s.refresh(obj); return obj

    async def accounts(self, user_id: int):
        async with self.sessions() as s: return list((await s.scalars(select(Account).where(Account.telegram_user_id == user_id, Account.enabled))).all())

    async def all_accounts(self):
        async with self.sessions() as s: return list((await s.scalars(select(Account).where(Account.enabled))).all())

    async def get_account(self, account_id: int):
        async with self.sessions() as s: return await s.get(Account, account_id)

    async def update_account(self, account_id: int, **values):
        async with self.sessions() as s:
            await s.execute(update(Account).where(Account.id == account_id).values(**values)); await s.commit()

    async def toggle_account_notification(self, account_id: int, field: str):
        allowed = {"notify_account_added", "notify_found", "notify_no_free_ip", "notify_permission", "notify_network", "notify_rate_limit", "notify_server", "notify_unknown"}
        if field not in allowed: raise ValueError("Unknown notification setting")
        async with self.sessions() as s:
            account = await s.get(Account, account_id)
            if not account: return None
            setattr(account, field, not getattr(account, field)); await s.commit(); await s.refresh(account); return account

    async def delete_account(self, account_id: int):
        async with self.sessions() as s:
            await s.execute(delete(FoundIP).where(FoundIP.account_id == account_id))
            await s.execute(delete(HunterTask).where(HunterTask.account_id == account_id))
            await s.execute(delete(AccountSubnet).where(AccountSubnet.account_id == account_id))
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
                if item["region"] != account.region:
                    raise ValueError("Подсеть принадлежит другому региону аккаунта")
                existing = await s.scalar(select(AccountSubnet).where(and_(AccountSubnet.account_id == account_id, AccountSubnet.subnet_id == item["subnet_id"])))
                if existing: existing.enabled = True; existing.cidr = item["cidr"]; existing.region = item["region"]
                else: s.add(AccountSubnet(account_id=account_id, **item))
            await s.commit()

    async def enabled_subnets(self, account_id: int):
        async with self.sessions() as s:
            account = await s.get(Account, account_id)
            region = account.region if account else "ru-3"
            return list((await s.scalars(select(AccountSubnet).where(AccountSubnet.account_id == account_id, AccountSubnet.enabled, AccountSubnet.region == region))).all())

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
