import logging
import time
from collections import defaultdict

from aiogram import Bot
from aiogram.exceptions import TelegramRetryAfter
from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup

from app.bot.formatting import error_message, found_message

log = logging.getLogger(__name__)


class NotificationService:
    """Single entry point for account and scheduler notifications."""

    def __init__(self, bot: Bot, repo):
        self.bot = bot
        self.repo = repo
        self._last_sent: dict[tuple[int, str, str], float] = {}
        self._suppressed: defaultdict[tuple[int, str, str], int] = defaultdict(int)

    @staticmethod
    def _kind(event):
        return "FOUND" if event == "FOUND" else str(event).split(":", 1)[0].strip()

    @staticmethod
    def _preference(kind):
        return {
            "FOUND": "notify_found",
            "NO_FREE_IP": "notify_no_free_ip",
            "PERMISSION_ERROR": "notify_permission",
            "AUTH_ERROR": "notify_permission",
            "NETWORK_ERROR": "notify_network",
            "TIMEOUT": "notify_network",
            "RATE_LIMIT": "notify_rate_limit",
            "SERVER_ERROR": "notify_server",
            "RECOVERED": "notify_recovered",
            "SCHEDULER": "notify_scheduler",
        }.get(kind, "notify_unknown")

    async def send(self, task, ip, fip_id, elapsed, event):
        account = await self.repo.get_account(task.account_id)
        if not account or not account.notifications_enabled:
            return False
        kind = self._kind(event)
        preference = self._preference(kind)
        if not getattr(account, preference, True):
            return False

        settings = await self.repo.notification_settings()
        if not settings.enabled:
            return False

        key = (account.id, getattr(task, "subnet_id", ""), kind)
        now = time.monotonic()
        window = max(0, int(settings.aggregate_seconds or 0))
        repeated = self._suppressed.pop(key, 0)
        if window and kind not in {"FOUND", "PERMISSION_ERROR", "AUTH_ERROR", "RECOVERED", "SCHEDULER"}:
            previous = self._last_sent.get(key)
            if previous is not None and now - previous < window:
                self._suppressed[key] += 1
                return False
        self._last_sent[key] = now

        display_event = event
        if repeated:
            display_event = f"{event}: ещё повторений за период: {repeated}"
        text = found_message(account, task, ip, fip_id, elapsed) if event == "FOUND" else error_message(account, task, display_event)
        markup = None
        if kind in {"PERMISSION_ERROR", "AUTH_ERROR"}:
            markup = InlineKeyboardMarkup(inline_keyboard=[[
                InlineKeyboardButton(text="▶️ Возобновить аккаунт", callback_data=f"scheduler:resume:{account.id}")
            ]])
        chat_id = account.topic_chat_id or task.telegram_user_id
        thread_id = account.topic_thread_id if account.topic_chat_id else None
        try:
            await self.bot.send_message(chat_id, text, message_thread_id=thread_id, reply_markup=markup)
            return True
        except TelegramRetryAfter as exc:
            log.warning("Telegram notification throttled account=%s retry_after=%s", account.id, exc.retry_after)
        except Exception:
            log.exception("notification delivery failed account=%s kind=%s", account.id, kind)
        return False

    async def send_report(self, user_id):
        settings = await self.repo.notification_settings()
        if not settings.enabled or settings.report_period == "off":
            return False
        accounts = await self.repo.accounts(user_id)
        if not accounts:
            return False
        lines = ["📊 <b>Сводный отчёт Selectel Hunter</b>", ""]
        total = found = errors = 0
        for account in accounts:
            values = await self.repo.account_statistics(account.id)
            by_result = values["by_result"]
            account_found = by_result.get("FOUND", {}).get("count", 0)
            account_errors = sum(item["count"] for key, item in by_result.items() if key not in {"FOUND", "NO_FREE_IP"})
            total += values["total"]; found += account_found; errors += account_errors
            lines.append(f"👤 <b>{account.display_name}</b> · {account.scheduler_status} · запросов: {values['total']} · IP: {account_found}")
        lines += ["", f"Всего запросов: <b>{total}</b>", f"Найдено IP: <b>{found}</b>", f"Ошибок: <b>{errors}</b>"]
        try:
            await self.bot.send_message(user_id, "\n".join(lines))
            return True
        except TelegramRetryAfter as exc:
            log.warning("report delivery throttled user=%s retry_after=%s", user_id, exc.retry_after)
        except Exception:
            log.exception("report delivery failed user=%s", user_id)
        return False
