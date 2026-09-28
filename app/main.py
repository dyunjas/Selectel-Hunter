import asyncio

from aiogram import Bot, Dispatcher
from aiogram.client.default import DefaultBotProperties
from aiogram.enums import ParseMode

from app.bot import build_router
from app.bot.multisubnet import build_multisubnet_router
from app.bot.formatting import error_message, found_message
from app.config import settings
from app.db.database import Database
from app.db.repositories import Repository
from app.hunter.manager import TaskManager
from app.config.subnets import TARGET_SUBNETS
from app.selectel.client import SelectelClient
from app.services.crypto import SecretBox
from app.services.logger import configure_logging


async def main():
    configure_logging(settings.log_level)
    if not settings.bot_token or not settings.admin_ids:
        raise RuntimeError("BOT_TOKEN and ADMIN_IDS must be configured")
    db = Database(settings.database_url)
    await db.init()
    repo = Repository(db.sessions)
    box = SecretBox(settings.encryption_key)
    clients = {}

    async def client_factory(account_id):
        account = await repo.get_account(account_id)
        proxy = box.decrypt(account.encrypted_proxy_url) if account.encrypted_proxy_url else None
        fingerprint = (account.encrypted_password, account.encrypted_proxy_url, account.network_api_url, account.username, account.project_id)
        cached = clients.get(account_id)
        if cached and cached[0] == fingerprint:
            return cached[1]
        if cached:
            await cached[1].close()
        client = SelectelClient(account, box.decrypt(account.encrypted_password), settings.floating_network_id, proxy)
        clients[account_id] = (fingerprint, client)
        return client

    async def notify(task, ip, fip_id, elapsed, event):
        account = await repo.get_account(task.account_id)
        if account:
            kind = "FOUND" if event == "FOUND" else str(event).split(":", 1)[0]
            preference = {"FOUND": "notify_found", "NO_FREE_IP": "notify_no_free_ip", "PERMISSION_ERROR": "notify_permission", "NETWORK_ERROR": "notify_network", "RATE_LIMIT": "notify_rate_limit", "SERVER_ERROR": "notify_server", "AUTH_ERROR": "notify_permission", "UNKNOWN": "notify_unknown"}.get(kind, "notify_unknown")
            if not getattr(account, preference, True): return
        chat_id = account.topic_chat_id if account and account.topic_chat_id else task.telegram_user_id
        thread_id = account.topic_thread_id if account and account.topic_thread_id else None
        text = found_message(account, task, ip, fip_id, elapsed) if event == "FOUND" else error_message(account, task, event)
        await bot.send_message(chat_id, text, message_thread_id=thread_id)

    manager = TaskManager(repo, client_factory, notify)
    bot = Bot(settings.bot_token, default=DefaultBotProperties(parse_mode=ParseMode.HTML))
    dispatcher = Dispatcher()
    dispatcher.include_router(build_multisubnet_router(repo, manager, set(settings.admin_ids)))
    dispatcher.include_router(build_router(repo, manager, box, client_factory, set(settings.admin_ids), bot, settings.notification_chat_id))
    await manager.restore_tasks()
    for account in await repo.all_accounts():
        await repo.set_subnets(account.id, TARGET_SUBNETS)
        if account.auto_start:
            for subnet in await repo.enabled_subnets(account.id):
                await manager.start_pair(account.telegram_user_id, account, subnet, settings.floating_network_id)
    try:
        await dispatcher.start_polling(bot)
    finally:
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
