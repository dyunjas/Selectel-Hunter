from html import escape


def safe(value) -> str:
    return escape(str(value if value is not None else "—"))


def found_message(account, task, ip, fip_id, elapsed):
    return ("<b>✅ Floating IP найден</b>\n\n"
            f"<blockquote><b>Аккаунт:</b> {safe(account.display_name if account else task.account_id)}\n"
            f"<b>Подсеть:</b> <code>{safe(task.subnet_cidr)}</code>\n"
            f"<b>IP:</b> <code>{safe(ip)}</code>\n"
            f"<b>Floating IP ID:</b> <code>{safe(fip_id)}</code></blockquote>\n"
            f"Попытка: <b>{task.attempts}</b>\nВремя поиска: <b>{elapsed:.1f} сек.</b>\n\n"
            "<i>Задача остановлена автоматически. Найденный IP не удалён.</i>")


def error_message(account, task, event):
    raw_event = str(event)
    error_kind, _, detail = raw_event.partition(":")
    error_kind = error_kind.strip()
    detail = detail.strip() or "Подробности не переданы"
    if error_kind == "NO_FREE_IP":
        title = "<b>ℹ️ Свободных IP пока нет</b>"
        reason = "В выбранной подсети сейчас нет доступных адресов."
    else:
        title = "<b>⚠️ Ошибка запроса IP</b>"
        reason = f"Тип: <code>{safe(error_kind)}</code>\n<b>Подробности:</b> <code>{safe(detail[:700])}</code>"
    follow_up = "Поиск продолжается с обычным интервалом аккаунта."
    return (title + "\n\n"
            f"<blockquote><b>Аккаунт:</b> {safe(account.display_name if account else task.account_id)}\n"
            f"<b>Подсеть:</b> <code>{safe(task.subnet_cidr)}</code>\n"
            f"{reason}</blockquote>\n"
            f"Попытка: <b>{task.attempts}</b>\n\n"
            f"<i>{follow_up}</i>")
