from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup


def main_menu():
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="👤 Аккаунты", callback_data="account:list"), InlineKeyboardButton(text="🚀 Запустить все", callback_data="hunt:all")],
        [InlineKeyboardButton(text="▶️ Выбрать аккаунт для запуска", callback_data="hunt:start")],
        [InlineKeyboardButton(text="📊 Расписание", callback_data="scheduler:view"), InlineKeyboardButton(text="🎯 Подсети", callback_data="targets:view")],
        [InlineKeyboardButton(text="🌍 Регионы", callback_data="regions:view"), InlineKeyboardButton(text="✅ Найденные IP", callback_data="found:list")],
        [InlineKeyboardButton(text="⚙️ Настройки поиска", callback_data="scheduler:settings"), InlineKeyboardButton(text="📈 Статистика", callback_data="stats:view")],
        [InlineKeyboardButton(text="🔔 Уведомления", callback_data="notifications:global"), InlineKeyboardButton(text="ℹ️ Помощь", callback_data="help")],
        [InlineKeyboardButton(text="🔄 Обновить статус", callback_data="home")],
    ])


def back(callback="home", label="🔙 Назад"):
    return InlineKeyboardMarkup(inline_keyboard=[[InlineKeyboardButton(text=label, callback_data=callback)]])


ACCOUNT_PAGE_SIZE = 12


def _account_rows(items, page, *, hunting=False):
    items = list(items)
    pages = max(1, (len(items) + ACCOUNT_PAGE_SIZE - 1) // ACCOUNT_PAGE_SIZE)
    page = max(0, min(page, pages - 1))
    columns = 1 if len(items) <= 4 else 2
    buttons = []
    status_icons = {"RUNNING": "🟢", "PAUSED": "⏸", "BLOCKED": "⛔", "RATE_LIMIT_COOLDOWN": "⏳"}
    for account in items[page * ACCOUNT_PAGE_SIZE:(page + 1) * ACCOUNT_PAGE_SIZE]:
        name = " ".join(account.display_name.split()) or f"#{account.id}"
        limit = 22 if columns == 2 else 36
        if len(name) > limit:
            name = name[:limit - 1] + "…"
        icon = "▶️" if hunting else status_icons.get(account.scheduler_status, "👤")
        prefix = "hunt:account" if hunting else "account:view"
        buttons.append(InlineKeyboardButton(text=f"{icon} {name}", callback_data=f"{prefix}:{account.id}"))
    rows = [buttons[i:i + columns] for i in range(0, len(buttons), columns)]
    if pages > 1:
        prefix = "hunt:page" if hunting else "account:page"
        navigation = []
        if page > 0:
            navigation.append(InlineKeyboardButton(text="‹", callback_data=f"{prefix}:{page - 1}"))
        navigation.append(InlineKeyboardButton(text=f"{page + 1}/{pages}", callback_data="keyboard:noop"))
        if page + 1 < pages:
            navigation.append(InlineKeyboardButton(text="›", callback_data=f"{prefix}:{page + 1}"))
        rows.append(navigation)
    return rows


def accounts(items, page=0):
    rows = _account_rows(items, page)
    rows.append([InlineKeyboardButton(text="➕ Добавить", callback_data="account:add"), InlineKeyboardButton(text="🏠 Меню", callback_data="home")])
    return InlineKeyboardMarkup(inline_keyboard=rows)


def hunt_accounts(items, page=0):
    return InlineKeyboardMarkup(inline_keyboard=_account_rows(items, page, hunting=True) + [[InlineKeyboardButton(text="🏠 Главное меню", callback_data="home")]])


def subnets(items, selected, back_callback="hunt:back"):
    rows = [[InlineKeyboardButton(text=("✅ " if x["subnet_id"] in selected else "▫️ ") + f"{x['region']} · {x['cidr']}", callback_data=f"subnet:toggle:{x['subnet_id']}")] for x in items]
    rows += [[InlineKeyboardButton(text="✅ Все", callback_data="subnet:all"), InlineKeyboardButton(text="▫️ Сбросить", callback_data="subnet:none")], [InlineKeyboardButton(text=f"🚀 Сохранить и запустить · {len(selected)} выбрано", callback_data="subnet:save")], [InlineKeyboardButton(text="🔙 К аккаунту", callback_data=back_callback)]]
    return InlineKeyboardMarkup(inline_keyboard=rows)


def delete_confirmation(account_id):
    return InlineKeyboardMarkup(inline_keyboard=[[InlineKeyboardButton(text="🗑 Да, удалить", callback_data=f"account:delete_yes:{account_id}")], [InlineKeyboardButton(text="Отмена", callback_data=f"account:view:{account_id}")]])


def task_list(items):
    return InlineKeyboardMarkup(inline_keyboard=[[InlineKeyboardButton(text=f"🔄 #{task.id} · {task.subnet_cidr}", callback_data=f"task:view:{task.id}")] for task in items] + [[InlineKeyboardButton(text="🏠 Главное меню", callback_data="home")]])


def task_actions(task_id):
    return InlineKeyboardMarkup(inline_keyboard=[[InlineKeyboardButton(text="⏸ Пауза", callback_data=f"task:pause:{task_id}"), InlineKeyboardButton(text="⏹ Остановить", callback_data=f"task:stop:{task_id}")], [InlineKeyboardButton(text="🔙 К задачам", callback_data="task:list")]])


def scheduler_settings(settings):
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text=f"⚡ Пауза между запросами: {settings.burst_request_delay:g} сек", callback_data="burst:delay")],
        [InlineKeyboardButton(text=f"🔄 Период BURST аккаунта: {settings.burst_cooldown} сек", callback_data="burst:cooldown")],
        [InlineKeyboardButton(text=f"🌐 Тайм-аут API: {settings.api_timeout:g} сек", callback_data="burst:timeout")],
        [InlineKeyboardButton(text=f"⚠️ Лимит критических ошибок: {settings.errors_before_disable}", callback_data="burst:errors")],
        [InlineKeyboardButton(text="📋 Открыть расписание", callback_data="scheduler:view")],
        [InlineKeyboardButton(text="🔙 Назад", callback_data="home")],
    ])


def account_burst_settings(settings, account_id):
    keyboard = scheduler_settings(settings).inline_keyboard[:-1]
    keyboard.append([InlineKeyboardButton(text="🌐 Прокси аккаунта", callback_data=f"account:proxy:{account_id}")])
    keyboard.append([InlineKeyboardButton(text="🔔 Уведомления", callback_data=f"account:notifications:{account_id}")])
    keyboard.append([InlineKeyboardButton(text="🔙 К аккаунту", callback_data=f"account:view:{account_id}")])
    return InlineKeyboardMarkup(inline_keyboard=keyboard)


def region_settings(states):
    rows = []
    for state in states:
        mark = "✅" if state.enabled else "▫️"
        rows.append([InlineKeyboardButton(text=f"{mark} {state.region}", callback_data=f"region:toggle:{state.region}")])
    rows.append([InlineKeyboardButton(text="🔙 Назад", callback_data="home")])
    return InlineKeyboardMarkup(inline_keyboard=rows)


# Keep the legacy account-region picker compatible with all configured regions.
def region_picker(prefix="region"):
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="🌍 ru-1", callback_data=f"{prefix}:ru-1"), InlineKeyboardButton(text="🌍 ru-3", callback_data=f"{prefix}:ru-3"), InlineKeyboardButton(text="🌍 ru-9", callback_data=f"{prefix}:ru-9")],
        [InlineKeyboardButton(text="🔙 Назад", callback_data="home")],
    ])


def notification_settings(account):
    def mark(value):
        return "✅" if value else "▫️"

    fields = [
        ("notify_account_added", "Добавление аккаунта"),
        ("notify_found", "Найден IP"),
        ("notify_no_free_ip", "Нет свободного IP"),
        ("notify_permission", "Доступ и авторизация"),
        ("notify_network", "Сетевые ошибки"),
        ("notify_rate_limit", "Ограничения API"),
        ("notify_server", "Ошибки сервера"),
        ("notify_unknown", "Другие ошибки"),
        ("notify_recovered", "Аккаунт восстановлен"),
        ("notify_scheduler", "Изменения расписания"),
    ]
    rows = [[InlineKeyboardButton(text=f"{'✅' if account.notifications_enabled else '▫️'} Все уведомления", callback_data=f"notify:master:{account.id}")]]
    rows += [[InlineKeyboardButton(text=f"{mark(getattr(account, field))} {label}", callback_data=f"notify:toggle:{account.id}:{field}")] for field, label in fields]
    rows.append([InlineKeyboardButton(text="🔙 К аккаунту", callback_data=f"account:view:{account.id}")])
    return InlineKeyboardMarkup(inline_keyboard=rows)


def global_notification_settings(settings):
    mark = "✅" if settings.enabled else "▫️"
    reports = {"off": "выключены", "hour": "каждый час", "day": "раз в сутки"}.get(settings.report_period, settings.report_period)
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text=f"{mark} Все уведомления", callback_data="notify:global:toggle")],
        [InlineKeyboardButton(text=f"🧹 Объединять повторы: {settings.aggregate_seconds} сек", callback_data="notify:global:aggregate")],
        [InlineKeyboardButton(text=f"📊 Отчёты: {reports}", callback_data="notify:global:reports")],
        [InlineKeyboardButton(text="🔙 Назад", callback_data="home")],
    ])


def account_settings_keyboard(account):
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="🌐 Настроить прокси", callback_data=f"account:proxy:{account.id}")],
        [InlineKeyboardButton(text="🔔 Уведомления", callback_data=f"account:notifications:{account.id}")],
        [InlineKeyboardButton(text="📈 Статистика аккаунта", callback_data=f"account:stats:{account.id}")],
        [InlineKeyboardButton(text="🌐 Проверить подключение", callback_data=f"account:check:{account.id}")],
        account_run_controls(account.id, account.scheduler_status),
        [InlineKeyboardButton(text="🗑 Удалить аккаунт", callback_data=f"account:delete:{account.id}")],
        [InlineKeyboardButton(text="🔙 К аккаунту", callback_data=f"account:view:{account.id}")],
    ])


def account_stats_periods(account_id, selected="all"):
    labels = [("1h", "1 час"), ("24h", "24 часа"), ("7d", "7 дней"), ("all", "Всё время")]
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text=("✅ " if value == selected else "") + label, callback_data=f"account:stats:{account_id}:{value}") for value, label in labels],
        [InlineKeyboardButton(text="🔙 К аккаунту", callback_data=f"account:view:{account_id}")],
    ])


def account_run_controls(account_id, status=None):
    if status in {"RUNNING", "RATE_LIMIT_COOLDOWN"}:
        return [InlineKeyboardButton(text="⏸ Поставить на паузу", callback_data=f"account:pause:{account_id}")]
    label = "▶️ Возобновить поиск" if status == "PAUSED" else "▶️ Запустить поиск"
    return [InlineKeyboardButton(text=label, callback_data=f"account:resume:{account_id}")]


def account_actions(account_id, has_topic=False, status=None):
    target_label = "🎯 Подсети для поиска" if status in {"RUNNING", "RATE_LIMIT_COOLDOWN"} else "🎯 Выбрать подсети и запустить"
    rows = [
        [InlineKeyboardButton(text=target_label, callback_data=f"account:hunt:{account_id}")],
        [InlineKeyboardButton(text="⚙️ Настройки", callback_data=f"account:settings:{account_id}"), InlineKeyboardButton(text="📈 Статистика", callback_data=f"account:stats:{account_id}")],
        [InlineKeyboardButton(text="🌐 Проверить", callback_data=f"account:check:{account_id}"), InlineKeyboardButton(text="🔔 Уведомления", callback_data=f"account:notifications:{account_id}")],
        account_run_controls(account_id, status),
    ]
    if not has_topic:
        rows.append([InlineKeyboardButton(text="🧵 Создать тему", callback_data=f"account:topic:{account_id}")])
    rows.append([InlineKeyboardButton(text="🔄 Обновить", callback_data=f"account:view:{account_id}"), InlineKeyboardButton(text="🔙 Аккаунты", callback_data="account:list")])
    return InlineKeyboardMarkup(inline_keyboard=rows)


def wizard_controls(skip_proxy=False):
    rows = []
    if skip_proxy:
        rows.append([InlineKeyboardButton(text="Без прокси", callback_data="account:add:skip_proxy")])
    rows.append([InlineKeyboardButton(text="✖️ Отменить добавление", callback_data="home")])
    return InlineKeyboardMarkup(inline_keyboard=rows)


def schedule_controls(visible, page, pages):
    buttons = []
    for item in visible:
        account = item["account"]
        name = " ".join(account.display_name.split())
        if len(name) > 24:
            name = name[:23] + "…"
        buttons.append(InlineKeyboardButton(text=f"👤 {name}", callback_data=f"account:view:{account.id}"))
    rows = [buttons[index:index + 2] for index in range(0, len(buttons), 2)]
    if pages > 1:
        navigation = []
        if page:
            navigation.append(InlineKeyboardButton(text="‹ Назад", callback_data=f"scheduler:page:{page - 1}"))
        navigation.append(InlineKeyboardButton(text=f"{page + 1}/{pages}", callback_data="keyboard:noop"))
        if page + 1 < pages:
            navigation.append(InlineKeyboardButton(text="Далее ›", callback_data=f"scheduler:page:{page + 1}"))
        rows.append(navigation)
    rows += [
        [InlineKeyboardButton(text="🔄 Обновить", callback_data=f"scheduler:page:{page}"), InlineKeyboardButton(text="⚙️ Настройки", callback_data="scheduler:settings")],
        [InlineKeyboardButton(text="🏠 Главное меню", callback_data="home")],
    ]
    return InlineKeyboardMarkup(inline_keyboard=rows)


def account_return(account_id):
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="👤 К аккаунту", callback_data=f"account:view:{account_id}"), InlineKeyboardButton(text="📋 Расписание", callback_data="scheduler:view")],
        [InlineKeyboardButton(text="🏠 Главное меню", callback_data="home")],
    ])


def refresh_back(callback, back_callback="home"):
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="🔄 Обновить", callback_data=callback), InlineKeyboardButton(text="🔙 Назад", callback_data=back_callback)],
    ])


def found_pages(page, pages):
    rows = []
    navigation = []
    if page:
        navigation.append(InlineKeyboardButton(text="‹ Назад", callback_data=f"found:page:{page - 1}"))
    if pages > 1:
        navigation.append(InlineKeyboardButton(text=f"{page + 1}/{pages}", callback_data="keyboard:noop"))
    if page + 1 < pages:
        navigation.append(InlineKeyboardButton(text="Далее ›", callback_data=f"found:page:{page + 1}"))
    if navigation:
        rows.append(navigation)
    rows.append([InlineKeyboardButton(text="🔄 Обновить", callback_data=f"found:page:{page}"), InlineKeyboardButton(text="🏠 Главное меню", callback_data="home")])
    return InlineKeyboardMarkup(inline_keyboard=rows)
