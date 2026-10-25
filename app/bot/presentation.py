"""Shared text/keyboard vocabulary; contains no resource mutations."""
from datetime import datetime, timedelta, timezone
from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup
from app.config.regions import REGIONS
from .formatting import safe

RULE = "━━━━━━━━━━━━━━━━━━"
JOB_LABELS = {"pending": "⏳ Ожидает запуска", "running": "🟢 Поиск активен", "paused": "⏸ На паузе",
              "cancelled": "🛑 Остановлено", "failed": "🔐 Требуется вмешательство", "completed": "✅ IP найден"}
PROBLEMS = {
    "NO_FREE_IP": "Свободных IP пока нет. Поиск продолжится автоматически.",
    "NETWORK_ERROR": "Временная проблема соединения с Selectel. Повторим позже.",
    "SERVER_ERROR": "Selectel временно недоступен. Повторим позже.",
    "RATE_LIMIT": "Selectel ограничил частоту запросов. Поиск продолжится по расписанию.",
    "AUTH_ERROR": "Не удалось войти в Selectel. Проверьте данные аккаунта.",
    "PERMISSION_ERROR": "Недостаточно прав Selectel. Проверьте роли пользователя.",
    "QUOTA_EXCEEDED": "Исчерпана квота IP. Проверьте лимиты проекта Selectel.",
}


def utcnow():
    return datetime.now(timezone.utc).replace(tzinfo=None)


def regions_for(selection):
    if selection == "all":
        return tuple(REGIONS)
    if selection == "spb":
        return tuple(region for region in REGIONS if region != "ru-2")
    return (selection,) if selection in REGIONS else ()


def region_name(region):
    if region == "all":
        return "Москва + Санкт-Петербург"
    if region == "spb":
        return "Санкт-Петербург (" + ", ".join(regions_for("spb")) + ")"
    return f"Москва ({region})" if region == "ru-2" else f"Санкт-Петербург ({region})"


def number(value):
    return f"{value:,}".replace(",", " ")


def duration(seconds):
    if seconds is None:
        return "Нет данных"
    minutes = max(0, int(seconds)) // 60
    days, minutes = divmod(minutes, 1440)
    hours, minutes = divmod(minutes, 60)
    return (f"{days} д. " if days else "") + (f"{hours} ч. " if days or hours else "") + f"{minutes} мин."


def timestamp(value, offset=300):
    if not value:
        return "Нет данных"
    if value.tzinfo is None:
        value = value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone(timedelta(minutes=offset))).strftime("%d.%m.%Y %H:%M:%S")


def timezone_name(offset):
    sign = "+" if offset >= 0 else "−"
    hours, minutes = divmod(abs(offset), 60)
    return f"UTC{sign}{hours:02}:{minutes:02}"


def friendly_error(error):
    if not error:
        return "Нет"
    kind = error.split(":", 1)[0]
    if kind in PROBLEMS:
        return PROBLEMS[kind]
    if "unresolved" in error.lower() or "ownership" in error.lower():
        return "Проверяем исход предыдущего запроса. Новый IP до завершения проверки не создаётся."
    if "legacy" in error.lower():
        return "Задание перенесено со старой версии. Проверьте уже выделенные IP перед продолжением."
    return "Не удалось завершить проверку. Результаты сохранены; подробности доступны администратору."


def resource_status(value):
    return {"ACTIVE": "Подключён (ACTIVE)", "DOWN": "Выделен, не подключён (DOWN)", "ERROR": "Ошибка ресурса (ERROR)"}.get(value, value)


def keyboard(*rows):
    return InlineKeyboardMarkup(inline_keyboard=[[InlineKeyboardButton(text=label, callback_data=callback) for label, callback in row] for row in rows])


def region_keyboard(current, prefix="city", back="home"):
    return keyboard([(("✅ " if current == "ru-2" else "") + "🏙 Москва", f"{prefix}:ru-2")],
                    [(("✅ " if current == "spb" else "") + "🌉 Санкт-Петербург", f"{prefix}:spb")],
                    [(("✅ " if current == "all" else "") + "🏙 + 🌉 Оба города", f"{prefix}:all")],
                    [("◀️ Назад", back)])


def job_keyboard(job, account=None):
    rows = []
    if job.status in {"pending", "running"}:
        if account and account.scheduler_status in {"PAUSED", "STOPPED"}:
            rows.append([("▶️ Возобновить аккаунт", f"account:resume:{job.account_id}")])
        rows.append([("⏸ Пауза задания", f"job:pause:{job.id}"), ("🛑 Остановить", f"job:stop:{job.id}")])
    elif job.status in {"paused", "failed", "cancelled"}:
        rows.append([("▶️ Продолжить", f"job:resume:{job.id}"), ("🛑 Остановить", f"job:stop:{job.id}")])
    rows.extend([[('🔄 Обновить', f"job:view:{job.id}")], [("📋 Найденные IP", f"history:job:{job.id}:0")], [("👤 Аккаунт", f"account:view:{job.account_id}"), ("◀️ Мои задания", "task:list")]])
    return keyboard(*rows)


def job_text(job, state, account, offset, position=None, at=None):
    at = at or utcnow()
    status = JOB_LABELS.get(job.status, "Состояние неизвестно")
    if job.status in {"pending", "running"} and job.resource_json:
        status = "🔎 Проверяем предыдущий запрос"
    if job.status in {"running", "pending"} and account.scheduler_status == "PAUSED":
        status = "⏸ Аккаунт на паузе"
    elif job.status in {"running", "pending"} and account.scheduler_status == "STOPPED":
        status = "🛑 Аккаунт остановлен"
    elif job.status == "pending" and account.scheduler_status == "RATE_LIMIT_COOLDOWN":
        status = "⏳ Пауза по лимиту Selectel"
    active_time = None
    if state and state.timing_complete:
        active_time = state.active_seconds + (max(0, (at - state.active_since).total_seconds()) if state.active_since else 0)
    lines = [f"🎯 <b>ЗАДАНИЕ #{safe(job.id[:8])}</b>", RULE,
             f"🌍 {safe(region_name(job.region))}", f"Аккаунт: <b>{safe(account.display_name)}</b>",
             f"📡 Подсеть: <code>{safe(job.cidr)}</code>", f"{status}", "",
             f"⏱ Время поиска: <b>{duration(active_time)}</b>",
             f"С создания: {duration((at - job.created_at).total_seconds())}",
             f"🔄 Попыток: <b>{number(job.attempts)}</b>",
             f"✅ Найдено IP: <b>{int(bool(job.found_id))}</b> · ошибок: <b>{number(job.errors)}</b>", "",
             f"📍 Последний найденный IP: <code>{safe(job.found_ip or 'Нет данных')}</code>",
             f"Последняя попытка: {timestamp(state.last_attempt_at if state else None, offset)}"]
    if job.status == "pending":
        lines.append(f"Очередь аккаунта: {position if position is not None else 'Нет данных'}")
        planned = max(job.due_at, account.next_cycle_at or job.due_at, account.cooldown_until or job.due_at)
        lines.append(f"Не раньше: {timestamp(planned, offset)}")
    if job.status in {"paused", "cancelled", "failed"}:
        reason = "По запросу пользователя" if state and state.pause_reason == "user" else friendly_error(job.last_error)
        lines += [f"Причина: {reason}", f"С {timestamp(state.paused_at if state else None, offset)}"]
    elif account.scheduler_status in {"PAUSED", "STOPPED"}:
        lines += ["Причина: остановлен весь аккаунт", f"С {timestamp(state.paused_at if state else None, offset)}"]
    elif job.last_error:
        lines.append(f"⚠️ {friendly_error(job.last_error)}")
    lines += [RULE, f"Время: {timezone_name(offset)} · обновление одной карточки"]
    return "\n".join(lines)
