from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from .models import Base


class Database:
    def __init__(self, url: str):
        self.engine = create_async_engine(url, future=True)
        self.sessions = async_sessionmaker(self.engine, expire_on_commit=False, class_=AsyncSession)

    async def init(self):
        async with self.engine.begin() as conn:
            if self.engine.url.drivername == "sqlite+aiosqlite":
                await conn.execute(text("PRAGMA foreign_keys=ON"))
            await conn.run_sync(Base.metadata.create_all)
            if self.engine.url.drivername == "sqlite+aiosqlite":
                columns = {
                    "encrypted_proxy_url": "TEXT",
                    "min_interval": "INTEGER NOT NULL DEFAULT 5",
                    "max_interval": "INTEGER NOT NULL DEFAULT 10",
                    "topic_chat_id": "INTEGER",
                    "topic_thread_id": "INTEGER",
                    "notify_account_added": "INTEGER NOT NULL DEFAULT 1",
                    "notify_errors": "INTEGER NOT NULL DEFAULT 1",
                    "notify_found": "INTEGER NOT NULL DEFAULT 1",
                }
                existing = await conn.execute(text("PRAGMA table_info(accounts)"))
                names = {row[1] for row in existing.fetchall()}
                for name, definition in columns.items():
                    if name not in names:
                        await conn.execute(text(f"ALTER TABLE accounts ADD COLUMN {name} {definition}"))

    def session(self):
        return self.sessions()

    async def close(self):
        await self.engine.dispose()
