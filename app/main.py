import asyncio
import logging
from datetime import datetime, timedelta

from aiogram import Bot, Dispatcher
from aiogram.client.default import DefaultBotProperties
from aiogram.enums import ParseMode

from app.bot import build_router
from app.bot.multisubnet import build_multisubnet_router
from app.bot.scheduler_ui import build_scheduler_ui
from app.config import settings
from app.config.regions import REGIONS
from app.db.database import Database
from app.db.repositories import Repository
from app.hunter.account_manager import AccountSchedulerManager
from app.selectel.client import SelectelClient
from app.services.crypto import SecretBox
from app.services.logger import configure_logging
from app.services.notifications import NotificationService


log = logging.getLogger(__name__)


async def main():
    configure_logging(settings.log_level)
    if not settings.bot_token or not settings.admin_ids:
        raise RuntimeError("BOT_TOKEN and ADMIN_IDS must be configured")
    db = Database(settings.database_url)
    await db.init()
    repo = Repository(db.sessions)
    box = SecretBox(settings.encryption_key)
    clients = {}
    bot = Bot(settings.bot_token, default=DefaultBotProperties(parse_mode=ParseMode.HTML))
    notification_service = NotificationService(bot, repo)

    async def client_factory(account_id):
        account = await repo.get_account(account_id)
        proxy = box.decrypt(account.encrypted_proxy_url) if account.encrypted_proxy_url else None
        # The client is account-scoped for auth/proxy only. Each resource request
        # selects its own endpoint from REGIONS by target.region.
        region_config = REGIONS["ru-3"]
        fingerprint = (account.encrypted_password, account.encrypted_proxy_url, account.username, account.project_id)
        cached = clients.get(account_id)
        if cached and cached[0] == fingerprint:
            return cached[1]
        if cached:
            await cached[1].close()
        scheduler_settings = await repo.scheduler_settings()
        client = SelectelClient(account, box.decrypt(account.encrypted_password), proxy=proxy, region="ru-3", network_api_url=region_config["network_api_url"], floating_network_id=region_config["floating_network_id"], api_timeout=scheduler_settings.api_timeout or settings.api_timeout)
        clients[account_id] = (fingerprint, client)
        return client

    manager = AccountSchedulerManager(repo, client_factory, notification_service.send, default_cooldown=settings.account_cooldown, stagger_seconds=settings.manual_stagger)
    dispatcher = Dispatcher()
    dispatcher.include_router(build_multisubnet_router(repo, manager, set(settings.admin_ids)))
    dispatcher.include_router(build_scheduler_ui(repo, manager))
    dispatcher.include_router(build_router(repo, manager, box, client_factory, set(settings.admin_ids), bot, settings.notification_chat_id))
    async def report_loop():
        periods = {"hour": timedelta(hours=1), "day": timedelta(days=1)}
        while True:
            try:
                await asyncio.sleep(60)
                report_settings = await repo.notification_settings()
                period = periods.get(report_settings.report_period)
                if not period:
                    continue
                now = datetime.utcnow()
                if report_settings.last_report_at and now - report_settings.last_report_at < period:
                    continue
                for user_id in await repo.notification_users():
                    await notification_service.send_report(user_id)
                await repo.update_notification_settings(last_report_at=now)
            except asyncio.CancelledError:
                raise
            except Exception:
                log.exception("notification report loop failed")

    report_task = asyncio.create_task(report_loop(), name="notification-reports")
    await manager.restore()
    try:
        await dispatcher.start_polling(bot)
    finally:
        report_task.cancel()
        await asyncio.gather(report_task, return_exceptions=True)
        await manager.shutdown()
        for _, client in clients.values():
            await client.close()
        await bot.session.close()
        await db.close()


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        print("Бот остановлен пользователем")
