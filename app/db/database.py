from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from .models import Base
from app.config.regions import REGIONS
from app.config.subnets import TARGET_SUBNETS


class Database:
    def __init__(self, url: str):
        self.engine = create_async_engine(url, future=True)
        self.sessions = async_sessionmaker(self.engine, expire_on_commit=False, class_=AsyncSession)

    async def init(self):
        async with self.engine.begin() as conn:
            if self.engine.url.drivername == "sqlite+aiosqlite":
                await conn.execute(text("PRAGMA foreign_keys=ON"))
            await conn.run_sync(Base.metadata.create_all)
            for region in REGIONS:
                await conn.execute(text("INSERT OR IGNORE INTO region_states(region, enabled) VALUES (:region, 1)"), {"region": region})
            for order_index, subnet in enumerate(TARGET_SUBNETS):
                await conn.execute(text("INSERT OR IGNORE INTO target_subnets(subnet_id, region, cidr, enabled, order_index) VALUES (:id, :region, :cidr, 1, :order_index)"), {"id": subnet["subnet_id"], "region": subnet["region"], "cidr": subnet["cidr"], "order_index": order_index})
                await conn.execute(text("UPDATE target_subnets SET region = :region, cidr = :cidr, order_index = :order_index WHERE subnet_id = :id"), {"id": subnet["subnet_id"], "region": subnet["region"], "cidr": subnet["cidr"], "order_index": order_index})
            if self.engine.url.drivername == "sqlite+aiosqlite":
                columns = {
                    "encrypted_proxy_url": "TEXT",
                    "region": "TEXT NOT NULL DEFAULT 'ru-3'",
                    "min_interval": "INTEGER NOT NULL DEFAULT 30",
                    "max_interval": "INTEGER NOT NULL DEFAULT 60",
                    "topic_chat_id": "INTEGER",
                    "topic_thread_id": "INTEGER",
                    "notify_account_added": "INTEGER NOT NULL DEFAULT 1",
                    "notify_errors": "INTEGER NOT NULL DEFAULT 1",
                    "notify_found": "INTEGER NOT NULL DEFAULT 1",
                    "notify_no_free_ip": "INTEGER NOT NULL DEFAULT 1",
                    "notify_permission": "INTEGER NOT NULL DEFAULT 1",
                    "notify_network": "INTEGER NOT NULL DEFAULT 1",
                    "notify_rate_limit": "INTEGER NOT NULL DEFAULT 1",
                    "notify_server": "INTEGER NOT NULL DEFAULT 1",
                    "notify_unknown": "INTEGER NOT NULL DEFAULT 1",
                    "scheduler_status": "TEXT NOT NULL DEFAULT 'IDLE'",
                    "current_subnet_index": "INTEGER NOT NULL DEFAULT 0",
                    "last_request_at": "DATETIME",
                    "next_request_at": "DATETIME",
                    "cooldown_until": "DATETIME",
                    "consecutive_network_errors": "INTEGER NOT NULL DEFAULT 0",
                    "scheduler_position": "INTEGER NOT NULL DEFAULT 0",
                    "last_cycle_started_at": "DATETIME",
                    "last_cycle_finished_at": "DATETIME",
                    "next_cycle_at": "DATETIME",
                    "burst_subnet_delay": "REAL NOT NULL DEFAULT 0.3",
                    "account_cooldown": "INTEGER NOT NULL DEFAULT 360",
                    "auto_stagger": "INTEGER NOT NULL DEFAULT 1",
                    "manual_stagger": "INTEGER",
                    "api_timeout": "REAL NOT NULL DEFAULT 5",
                    "errors_before_disable": "INTEGER NOT NULL DEFAULT 30",
                    "stop_account_after_found": "INTEGER NOT NULL DEFAULT 0",
                }
                existing = await conn.execute(text("PRAGMA table_info(accounts)"))
                names = {row[1] for row in existing.fetchall()}
                for name, definition in columns.items():
                    if name not in names:
                        await conn.execute(text(f"ALTER TABLE accounts ADD COLUMN {name} {definition}"))
                await conn.execute(text("UPDATE accounts SET min_interval = 30 WHERE min_interval < 30"))
                await conn.execute(text("UPDATE accounts SET max_interval = 60 WHERE max_interval < min_interval"))
            if self.engine.url.drivername == "sqlite+aiosqlite":
                subnet_columns = {"region": "TEXT NOT NULL DEFAULT 'ru-3'"}
                existing_subnets = await conn.execute(text("PRAGMA table_info(account_subnets)"))
                subnet_names = {row[1] for row in existing_subnets.fetchall()}
                for name, definition in subnet_columns.items():
                    if name not in subnet_names:
                        await conn.execute(text(f"ALTER TABLE account_subnets ADD COLUMN {name} {definition}"))
                found_columns = await conn.execute(text("PRAGMA table_info(found_ips)"))
                found_names = {row[1] for row in found_columns.fetchall()}
                if "region" not in found_names:
                    await conn.execute(text("ALTER TABLE found_ips ADD COLUMN region TEXT NOT NULL DEFAULT 'ru-3'"))
                for item in TARGET_SUBNETS:
                    await conn.execute(
                        text("UPDATE account_subnets SET region = :region WHERE subnet_id = :subnet_id"),
                        {"region": item["region"], "subnet_id": item["subnet_id"]},
                    )

    def session(self):
        return self.sessions()

    async def close(self):
        await self.engine.dispose()
