import asyncio
import logging
import json
import os
from datetime import datetime

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
from app.db.migration import migrate_queue
from app.bot.jobs import build_jobs_router
from app.bot.experience import build_experience_router
from app.bot.screens import UiScreens
from app.bot.live import LiveCards
from app.bot.safety import UiErrorBoundary, build_stale_router
from app.bot.polling import resilient_polling, shutdown_signals
from app.services.runtime import InstanceLock, write_health


log = logging.getLogger(__name__)


async def main():
    configure_logging(settings.log_level)
    if not settings.bot_token or not settings.admin_ids:
        raise RuntimeError("BOT_TOKEN and ADMIN_IDS must be configured")
    db = Database(settings.database_url)
    await db.init()
    repo = Repository(db.sessions)
    await migrate_queue(repo)
    box = SecretBox(settings.encryption_key)
    clients = {}
    bot = Bot(settings.bot_token, default=DefaultBotProperties(parse_mode=ParseMode.HTML))
    notification_service = NotificationService(bot, repo)

    async def client_factory(account_id):
        account = await repo.get_account(account_id)
        proxy = box.decrypt(account.encrypted_proxy_url) if account.encrypted_proxy_url else None
        # The client is account-scoped for auth/proxy only. Each resource request
        # selects its own endpoint from REGIONS by target.region.
        region_config = REGIONS[account.region]
        scheduler_settings = await repo.scheduler_settings()
        fingerprint = (account.encrypted_password, account.encrypted_proxy_url, account.username, account.project_id, account.domain, account.auth_url, account.region, scheduler_settings.api_timeout)
        cached = clients.get(account_id)
        if cached and cached[0] == fingerprint:
            return cached[1]
        if cached:
            await cached[1].close()
        client = SelectelClient(account, box.decrypt(account.encrypted_password), proxy=proxy, region=account.region, network_api_url=region_config["network_api_url"], floating_network_id=region_config["floating_network_id"], api_timeout=scheduler_settings.api_timeout or settings.api_timeout)
        clients[account_id] = (fingerprint, client)
        return client

    manager = AccountSchedulerManager(repo, client_factory, notification_service.send, default_cooldown=settings.account_cooldown, stagger_seconds=settings.manual_stagger)
    dispatcher = Dispatcher()
    screens = UiScreens(repo, manager)
    live_cards = LiveCards(bot, screens)
    dispatcher.callback_query.outer_middleware(live_cards)
    dispatcher.callback_query.outer_middleware(UiErrorBoundary())
    dispatcher.message.outer_middleware(live_cards)
    dispatcher.include_router(build_experience_router(repo, manager, set(settings.admin_ids), client_factory, screens))
    dispatcher.include_router(build_jobs_router(repo, manager, set(settings.admin_ids), live_cards, screens))
    dispatcher.include_router(build_multisubnet_router(repo, manager, set(settings.admin_ids), client_factory))
    dispatcher.include_router(build_scheduler_ui(repo, manager, set(settings.admin_ids)))
    dispatcher.include_router(build_router(repo, manager, box, client_factory, set(settings.admin_ids), bot, settings.notification_chat_id))
    dispatcher.include_router(build_stale_router(set(settings.admin_ids)))
    async def watchdog():
        while True:
            try:
                await repo.queue.recover()
                healthy = manager._scheduler_task is not None and not manager._scheduler_task.done()
                health = write_health(os.getenv("HEALTH_FILE", "health.json"), db.engine.url.database or ".", healthy)
                if not health["ok"]:
                    log.error("Healthcheck failed: scheduler=%s free_bytes=%s", healthy, health["free_bytes"])
                # Bound cached clients when accounts are removed.
                active_ids = {a.id for a in await repo.all_accounts()}
                for account_id in list(clients):
                    if account_id not in active_ids:
                        _, client = clients.pop(account_id)
                        await client.close()
                notification_service.prune()
                await repo.ui.prune()
            except asyncio.CancelledError:
                raise
            except Exception:
                log.exception("queue watchdog failed")
            await asyncio.sleep(15)

    async def deliver_notifications():
        from aiogram.exceptions import TelegramRetryAfter
        while True:
            try:
                for item in await repo.queue.pending_notifications():
                    try:
                        payload = json.loads(item.payload)
                        if "task_id" in payload:
                            task = await repo.get_task(payload["task_id"])
                            delivered = True if task is None else await notification_service.send(task, payload["ip"], payload["fip_id"], 0, "FOUND", durable=True)
                        else:
                            from types import SimpleNamespace
                            job = await repo.queue.get(item.job_id)
                            subject = SimpleNamespace(job_id=job.id, account_id=job.account_id, telegram_user_id=job.telegram_user_id,
                                subnet_id=job.subnet_id, subnet_cidr=job.cidr, region=job.region, attempts=job.attempts,
                                event_at=datetime.fromisoformat(payload["at"]) if payload.get("at") else None)
                            delivered = await notification_service.send(subject, None, None, 0, payload["event"], durable=True)
                        await repo.queue.notification_result(item, delivered, min(3600, 30 * 2 ** min(item.attempts, 7)))
                    except TelegramRetryAfter as exc:
                        await repo.queue.notification_result(item, False, exc.retry_after)
                    except Exception:
                        await repo.queue.notification_result(item, False, min(3600, 30 * 2 ** min(item.attempts, 7)))
                        log.warning("Deferred Telegram delivery failed outbox_id=%s", item.id)
            except asyncio.CancelledError:
                raise
            except Exception:
                log.exception("notification outbox loop failed")
            await asyncio.sleep(1)

    background = []
    try:
        await manager.restore()
        await repo.queue.announce_restore()
        background = [asyncio.create_task(watchdog(), name="queue-watchdog"),
                      asyncio.create_task(deliver_notifications(), name="notification-outbox"),
                      asyncio.create_task(live_cards.run(), name="live-job-cards")]
        stop = asyncio.Event()
        with shutdown_signals(stop):
            await resilient_polling(dispatcher, bot, stop)
    finally:
        for task in background:
            task.cancel()
        await asyncio.gather(*background, return_exceptions=True)
        await manager.shutdown()
        for _, client in clients.values():
            await client.close()
        await bot.session.close()
        await db.close()


if __name__ == "__main__":
    try:
        from sqlalchemy.engine import make_url
        database_path = make_url(settings.database_url).database or "selectel_hunter"
        with InstanceLock(f"{database_path}.lock"):
            asyncio.run(main())
    except KeyboardInterrupt:
        print("Бот остановлен пользователем")
