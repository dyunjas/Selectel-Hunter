from datetime import datetime, timedelta, timezone

from aiogram.exceptions import TelegramBadRequest

from .formatting import safe


SCHEDULE_PAGE_SIZE = 8
DISPLAY_TIMEZONE = timezone(timedelta(hours=5))
STATUS_LABELS = {
    "RUNNING": "🟢 В поиске",
    "WAITING": "⏳ По расписанию",
    "COOLDOWN": "⏳ Пауза",
    "RATE_LIMIT_COOLDOWN": "⏳ Ограничение API",
    "NETWORK_COOLDOWN": "⏳ Пауза после ошибки сети",
    "PAUSED": "⏸ На паузе",
    "STOPPED": "⏹ Остановлен",
    "BLOCKED": "⛔ Нужна проверка доступа",
    "IDLE": "⚪ Не запущен",
}


def status_label(status):
    return STATUS_LABELS.get(status, safe(status))


def short_name(name, limit=48):
    name = " ".join(name.split())
    return safe(name[:limit - 1] + "…" if len(name) > limit else name)


def local_time(value):
    if not value:
        return "не запланирован"
    if value.tzinfo is None:
        value = value.replace(tzinfo=timezone.utc)
    return value.astimezone(DISPLAY_TIMEZONE).strftime("%d.%m %H:%M:%S")


def page_slice(items, page, size=SCHEDULE_PAGE_SIZE):
    pages = max(1, (len(items) + size - 1) // size)
    page = max(0, min(page, pages - 1))
    return items[page * size:(page + 1) * size], page, pages


async def edit_screen(message, text, reply_markup):
    """Refreshing an unchanged screen should still acknowledge the button."""
    try:
        await message.edit_text(text, reply_markup=reply_markup)
    except TelegramBadRequest as exc:
        if "message is not modified" not in exc.message.lower():
            raise


async def overview(repo, manager, user_id):
    snapshot = await manager.get_scheduler_snapshot(user_id)
    settings = await repo.scheduler_settings()
    global_active_count = sum(manager._active(account) for account in await repo.all_accounts())
    target_count = len(await repo.enabled_targets())
    return dashboard_text(snapshot, settings.burst_cooldown, global_active_count, target_count)


def dashboard_text(snapshot, period, global_active_count, target_count):
    accounts = [item["account"] for item in snapshot["queue"]]
    active = sum(account.enabled and account.scheduler_status in {"RUNNING", "RATE_LIMIT_COOLDOWN"} for account in accounts)
    paused = sum(account.scheduler_status == "PAUSED" for account in accounts)
    blocked = sum(account.scheduler_status == "BLOCKED" for account in accounts)
    lines = [
        "🚀 <b>Selectel IP Hunter</b>", "",
        f"Аккаунты: <b>{len(accounts)}</b> · в поиске: <b>{active}</b>",
        f"Пауза: <b>{paused}</b> · требуют проверки: <b>{blocked}</b>",
        f"Выполняют BURST: <b>{len(snapshot['running_account_ids'])}</b>",
        f"Включённых подсетей: <b>{target_count}</b>", "",
        f"Каждый аккаунт стартует раз в <b>{period:g} сек</b>.",
    ]
    if global_active_count:
        lines.append(f"Интервал между аккаунтами: <b>{period / global_active_count:.2f} сек</b>.")
    if not accounts:
        lines += ["", "Начните с раздела «Аккаунты» → «Добавить»."]
    elif not target_count:
        lines += ["", "Выберите подсети для поиска в разделе «Подсети»."]
    elif not active:
        lines += ["", "Нажмите «Запустить все» или выберите один аккаунт."]
    else:
        lines += ["", "Подробности запусков — в разделе «Расписание»."]
    return "\n".join(lines)


def schedule_text(snapshot, period, global_active_count, page=0):
    queue = snapshot["queue"]
    visible, page, pages = page_slice(queue, page)
    running = set(snapshot["running_account_ids"])
    active = sum(item["account"].enabled and item["account"].scheduler_status in {"RUNNING", "RATE_LIMIT_COOLDOWN"} for item in queue)
    lines = [
        "📋 <b>Расписание BURST</b>", "",
        f"В поиске: <b>{active}</b> · выполняются: <b>{len(running)}</b> · ожидают: <b>{max(0, active - len(running))}</b>",
        f"Период аккаунта: <b>{period:g} сек</b>",
    ]
    if global_active_count:
        lines.append(f"Между запусками: <b>{period / global_active_count:.2f} сек</b> ({global_active_count} аккаунтов)")
    lines += ["Ответы других аккаунтов не задерживают запуск.", "Время: ЕКБ (UTC+5).", ""]
    for position, item in enumerate(visible, page * SCHEDULE_PAGE_SIZE + 1):
        account = item["account"]
        lines.append(f"<b>{position}. {short_name(account.display_name)}</b> · {status_label(item['status'])}")
        if account.id in running:
            lines.append("   BURST выполняется, ожидаются ответы")
        elif account.next_cycle_at:
            lines.append(f"   Следующий старт: <b>{local_time(account.next_cycle_at)}</b>")
        if account.scheduler_status == "RATE_LIMIT_COOLDOWN" and account.cooldown_until:
            lines.append(f"   Пауза API до {local_time(account.cooldown_until)}")
        lines.append("")
    if not queue:
        lines.append("Аккаунтов пока нет. Добавьте первый в разделе «Аккаунты».")
    lines.append(f"Страница {page + 1}/{pages} · обновлено {datetime.now(DISPLAY_TIMEZONE):%H:%M:%S}")
    return "\n".join(lines), visible, page, pages


HELP_TEXT = (
    "ℹ️ <b>Как пользоваться</b>\n\n"
    "1. <b>Аккаунты → Добавить</b>: введите данные Selectel и, при необходимости, прокси.\n"
    "2. <b>Подсети</b> и <b>Регионы</b>: включите нужные направления поиска. Эти настройки общие для всех аккаунтов.\n"
    "3. <b>Запустить все</b> или <b>Выбрать аккаунт</b>: начните поиск.\n"
    "4. <b>Расписание</b>: смотрите ближайшие старты, обновляйте экран и открывайте карточки аккаунтов.\n\n"
    "<b>Как работает BURST</b>\n"
    "Период задаётся в «Настройках поиска». Интервал между стартами = период / число активных аккаунтов. "
    "Ожидание ответов не задерживает другие аккаунты; дополнительной паузы после завершения нет.\n\n"
    "Найденные адреса доступны в разделе «Найденные IP». Временные сетевые ошибки не останавливают поиск.\n\n"
    "/start — главный экран · /status — состояние · /help — помощь · /cancel — отмена ввода"
)
