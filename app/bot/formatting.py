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
    return ("<b>⚠️ Ошибка запроса IP</b>\n\n"
            f"<blockquote><b>Аккаунт:</b> {safe(account.display_name if account else task.account_id)}\n"
            f"<b>Подсеть:</b> <code>{safe(task.subnet_cidr)}</code>\n"
            f"<b>Тип:</b> <code>{safe(event)}</code></blockquote>\n"
            f"Попытка: <b>{task.attempts}</b>\n\n"
            "<i>Поиск продолжается автоматически.</i>")
