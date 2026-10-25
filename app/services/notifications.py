import logging
import time

from aiogram import Bot
from aiogram.exceptions import TelegramRetryAfter
from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup

from app.bot.formatting import found_message
from app.bot.presentation import region_name, timestamp, RULE, safe, friendly_error

log = logging.getLogger(__name__)


class NotificationService:
    """Single entry point for account and scheduler notifications."""

    ALLOWED_EVENTS = frozenset({"PERMISSION_ERROR", "QUOTA_EXCEEDED"})

    def __init__(self, bot: Bot, repo):
        self.bot = bot
        self.repo = repo
        self._last_sent: dict[tuple[int, str, str], float] = {}

    def prune(self):
        cutoff = time.monotonic() - 86400
        for key in list(self._last_sent):
            if self._last_sent[key] < cutoff:
                self._last_sent.pop(key, None)

    @staticmethod
    def _kind(event):
        return "FOUND" if event == "FOUND" else str(event).split(":", 1)[0].strip()

    @staticmethod
    def _preference(kind):
        return {
            "FOUND": "notify_found",
            "NO_FREE_IP": "notify_no_free_ip",
            "PERMISSION_ERROR": "notify_permission",
            "QUOTA_EXCEEDED": None,
            "AUTH_ERROR": "notify_permission",
            "NETWORK_ERROR": "notify_network",
            "TIMEOUT": "notify_network",
            "RATE_LIMIT": "notify_rate_limit",
            "SERVER_ERROR": "notify_server",
            "RECOVERED": "notify_recovered",
            "STARTED": None,
            "PAUSED": None,
            "RESUMED": None,
            "STOPPED": None,
            "RESTORED": "notify_recovered",
            "SCHEDULER": "notify_scheduler",
        }.get(kind, "notify_unknown")

    async def send(self, task, ip, fip_id, elapsed, event, durable=False):
        kind = self._kind(event)
        if kind not in self.ALLOWED_EVENTS:
            # Consume old outbox entries too, so disabled notices are not retried.
            return durable
        account = await self.repo.get_account(task.account_id)
        if not account or not account.notifications_enabled:
            return durable
        preference = self._preference(kind)
        if preference and not getattr(account, preference, True):
            return durable

        settings = await self.repo.notification_settings()
        if not settings.enabled:
            return durable

        key = (account.id, getattr(task, "region", ""), kind)
        now = time.monotonic()
        window = max(0, int(settings.aggregate_seconds or 0))
        persistent_ui = getattr(self.repo, "ui", None)
        grouped = window and kind not in {"FOUND", "SCHEDULER", "STARTED", "PAUSED", "RESUMED", "STOPPED", "RESTORED"}
        cooldown_key = f"{account.id}:{getattr(task, 'region', '')}:{kind}"
        if grouped:
            previous = self._last_sent.get(key)
            suppressed = previous is not None and now - previous < window
            if persistent_ui:
                suppressed = suppressed or await self.repo.ui.notification_cooldown(cooldown_key, window)
            if suppressed:
                return durable
        markup = None
        job_id = getattr(task, "job_id", None)
        offset, found_at, found_item = 300, None, None
        if getattr(self.repo, "ui", None) is not None:
            pref = await self.repo.ui.preferences(task.telegram_user_id)
            offset = pref.utc_offset_minutes
            if kind == "FOUND":
                found_item = await self.repo.ui.found_item(task.telegram_user_id, task_id=task.id)
                if found_item is None:
                    return False
                job = await self.repo.ui.found_job(found_item)
                job_id, found_at = job.id if job else None, found_item.found_at
            elif job_id:
                job = await self.repo.queue.get(job_id)
                state = await self.repo.ui.job_state(job_id)
                # Obsolete lifecycle messages are coalesced; FOUND is never coalesced.
                if kind in {"STARTED", "RESUMED", "RESTORED"} and (job.status not in {"pending", "running"} or account.scheduler_status in {"PAUSED", "STOPPED"}):
                    return durable
                if kind == "PAUSED" and job.status != "paused" and account.scheduler_status != "PAUSED":
                    return durable
                if kind == "STOPPED" and job.status != "cancelled" and account.scheduler_status != "STOPPED":
                    return durable
                if kind == "RECOVERED" and state and state.problem_type:
                    return durable
        if kind == "FOUND":
            text = found_message(account, task, ip, fip_id, elapsed, job_id=job_id, found_at=found_at, offset=offset)
            rows = []
            if found_item:
                rows.append([InlineKeyboardButton(text="📋 Информация об IP", callback_data=f"ip:view:{found_item.id}")])
            if job_id:
                rows.append([InlineKeyboardButton(text="🎯 Открыть задание", callback_data=f"job:view:{job_id}")])
            markup = InlineKeyboardMarkup(inline_keyboard=rows) if rows else None
        else:
            titles = {"STARTED": "🟢 Поиск запущен", "PAUSED": "⏸ Поиск приостановлен", "RESUMED": "▶️ Поиск возобновлён",
                      "STOPPED": "🛑 Поиск остановлен", "RESTORED": "🔄 Поиск восстановлен после перезапуска",
                      "RECOVERED": "✅ Соединение с Selectel восстановлено", "AUTH_ERROR": "🔐 Требуется проверка авторизации",
                      "PERMISSION_ERROR": "🔐 Требуется проверка прав", "QUOTA_EXCEEDED": "⚠️ Исчерпана квота IP"}
            title = titles.get(kind, "⚠️ Временная проблема Selectel API")
            detail = "" if kind in titles and kind not in {"AUTH_ERROR", "PERMISSION_ERROR", "QUOTA_EXCEEDED"} else f"\n\n{friendly_error(event)}"
            text = (f"<b>{title}</b>\n{RULE}\n\n🌍 {safe(region_name(task.region))}\n"
                    f"📡 <code>{safe(task.subnet_cidr)}</code>\nАккаунт: <b>{safe(account.display_name)}</b>{detail}")
            if getattr(task, "event_at", None):
                text += f"\n🕒 {timestamp(task.event_at, offset)}"
            if job_id:
                markup = InlineKeyboardMarkup(inline_keyboard=[[InlineKeyboardButton(text="🎯 Открыть задание", callback_data=f"job:view:{job_id}")]])
        if kind in {"PERMISSION_ERROR", "AUTH_ERROR"} and not job_id:
            markup = InlineKeyboardMarkup(inline_keyboard=[[
                InlineKeyboardButton(text="▶️ Возобновить аккаунт", callback_data=f"scheduler:resume:{account.id}")
            ]])
        chat_id = account.topic_chat_id or task.telegram_user_id
        thread_id = account.topic_thread_id if account.topic_chat_id else None
        try:
            await self.bot.send_message(chat_id, text, message_thread_id=thread_id, reply_markup=markup)
            self._last_sent[key] = now
            if grouped and persistent_ui:
                await self.repo.ui.notification_cooldown(cooldown_key, window, mark=True)
            return True
        except TelegramRetryAfter as exc:
            if durable:
                raise
            log.warning("Telegram notification throttled account=%s retry_after=%s", account.id, exc.retry_after)
        except Exception:
            if durable:
                raise
            log.exception("notification delivery failed account=%s kind=%s", account.id, kind)
        return False

    async def send_report(self, user_id):
        # Only permission and quota alerts are enabled; statistics remain in the UI.
        return False
