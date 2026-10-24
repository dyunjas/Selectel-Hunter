"""Telegram reconnection is independent of the running search scheduler."""
import asyncio
import logging
import signal
from contextlib import contextmanager
from aiogram.types import BotCommand

log = logging.getLogger(__name__)


@contextmanager
def shutdown_signals(stop):
    loop = asyncio.get_running_loop()
    previous = {}
    for signum in (signal.SIGINT, signal.SIGTERM):
        previous[signum] = signal.getsignal(signum)
        signal.signal(signum, lambda *_: loop.call_soon_threadsafe(stop.set))
    try:
        yield
    finally:
        for signum, handler in previous.items():
            signal.signal(signum, handler)


async def resilient_polling(dispatcher, bot, stop, retry_seconds=5):
    async def poll():
        while not stop.is_set():
            try:
                await bot.set_my_commands([BotCommand(command=command, description=description) for command, description in (
                    ("start", "Главное меню"), ("status", "Активные задания"), ("stats", "Статистика"),
                    ("help", "Справка"), ("cancel", "Отмена ввода"))])
                await dispatcher.start_polling(bot, handle_signals=False, close_bot_session=False)
            except asyncio.CancelledError:
                raise
            except Exception:
                log.warning("Telegram polling unavailable; search continues")
            try:
                await asyncio.wait_for(stop.wait(), timeout=retry_seconds)
            except asyncio.TimeoutError:
                pass

    task = asyncio.create_task(poll(), name="telegram-polling")
    try:
        await stop.wait()
    finally:
        task.cancel()
        await asyncio.gather(task, return_exceptions=True)
