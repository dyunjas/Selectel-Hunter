from html import escape
from app.config.regions import REGIONS


def safe(value):
    return escape(str(value if value is not None else "—"))


def found_message(account, task, ip, fip_id, elapsed, job_id=None, found_at=None, offset=300):
    from .presentation import region_name, timestamp, number, RULE
    region = getattr(task, "region", None) or "—"
    return (
        f"🎉 <b>НАЙДЕН НУЖНЫЙ IP!</b>\n{RULE}\n\n"
        f"<blockquote><b>Аккаунт</b>  {safe(account.display_name if account else task.account_id)}\n"
        f"<b>Регион</b>  {safe(region_name(region))}\n"
        f"<b>Подсеть</b>  <code>{safe(task.subnet_cidr)}</code>\n"
        f"\n✅ <b>IP</b>  <code>{safe(ip)}</code></blockquote>\n"
        f"🎯 Задание: <b>#{safe(job_id[:8] if job_id else str(getattr(task, 'id', '—')))}</b>\n"
        f"🕒 Время: {timestamp(found_at or getattr(task, 'finished_at', None), offset)}\n"
        f"🔄 Попыток до находки: <b>{number(task.attempts)}</b>\n\n"
        "💾 Выделение IP подтверждено Selectel. Результат сохранён в БД.\n"
        "<i>Задание завершено, IP сохранён.</i>"
    )


def error_message(account, task, event):
    raw = str(event); kind, _, detail = raw.partition(":"); kind = kind.strip(); detail = detail.strip() or "Подробности не переданы"
    region = getattr(task, "region", None) or "—"
    if kind == "NO_FREE_IP":
        title = "<b>ℹ️ СВОБОДНЫХ IP ПОКА НЕТ</b>"; reason = "В выбранной подсети сейчас нет доступных адресов."
    else:
        title = "<b>⚠️ ОШИБКА ЗАПРОСА</b>"; reason = f"<b>Тип</b>  <code>{safe(kind)}</code>\n<b>Детали</b>  <code>{safe(detail[:700])}</code>"
    if kind in {"AUTH_ERROR", "PERMISSION_ERROR"}:
        footer = "<i>Поиск остановлен. Аккаунт заблокирован до исправления авторизации или прав.</i>"
    else:
        footer = "<i>Поиск продолжается с интервалом аккаунта.</i>"
    return (f"{title}\n\n<blockquote><b>Аккаунт</b>  {safe(account.display_name if account else task.account_id)}\n"
            f"<b>Регион</b>  <code>{safe(region)}</code>\n"
            f"<b>Подсеть</b>  <code>{safe(task.subnet_cidr)}</code>\n{reason}</blockquote>\n"
            f"Попытка  <b>{task.attempts}</b>\n\n{footer}")
