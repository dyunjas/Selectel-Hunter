from html import escape


def safe(value):
    return escape(str(value if value is not None else "—"))


def found_message(account, task, ip, fip_id, elapsed):
    return (
        "<b>✅ FLOATING IP НАЙДЕН</b>\n\n"
        f"<blockquote><b>Аккаунт</b>  {safe(account.display_name if account else task.account_id)}\n"
        f"<b>Подсеть</b>  <code>{safe(task.subnet_cidr)}</code>\n"
        f"<b>IP</b>  <code>{safe(ip)}</code>\n"
        f"<b>Floating IP ID</b>  <code>{safe(fip_id)}</code></blockquote>\n"
        f"Попытка  <b>{task.attempts}</b>\n"
        f"Время поиска  <b>{elapsed:.1f} сек.</b>\n\n"
        "<i>Задача остановлена автоматически. IP не удалён.</i>"
    )


def error_message(account, task, event):
    raw = str(event); kind, _, detail = raw.partition(":"); kind = kind.strip(); detail = detail.strip() or "Подробности не переданы"
    if kind == "NO_FREE_IP":
        title = "<b>ℹ️ СВОБОДНЫХ IP ПОКА НЕТ</b>"; reason = "В выбранной подсети сейчас нет доступных адресов."
    else:
        title = "<b>⚠️ ОШИБКА ЗАПРОСА</b>"; reason = f"<b>Тип</b>  <code>{safe(kind)}</code>\n<b>Детали</b>  <code>{safe(detail[:700])}</code>"
    return (f"{title}\n\n<blockquote><b>Аккаунт</b>  {safe(account.display_name if account else task.account_id)}\n"
            f"<b>Подсеть</b>  <code>{safe(task.subnet_cidr)}</code>\n{reason}</blockquote>\n"
            f"Попытка  <b>{task.attempts}</b>\n\n<i>Поиск продолжается с интервалом аккаунта.</i>")
