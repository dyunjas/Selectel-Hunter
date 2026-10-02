from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup


def region_picker(prefix="region"):
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="🌐 ru-3", callback_data=f"{prefix}:ru-3"), InlineKeyboardButton(text="🌐 ru-9", callback_data=f"{prefix}:ru-9")],
        [InlineKeyboardButton(text="🔙 Назад", callback_data="home")],
    ])


def main_menu():
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="👤 Аккаунты", callback_data="account:list"), InlineKeyboardButton(text="🚀 Запустить все", callback_data="hunt:all")],
        [InlineKeyboardButton(text="▶️ Запустить выборочно", callback_data="hunt:start")],
        [InlineKeyboardButton(text="📊 Расписание", callback_data="scheduler:view"), InlineKeyboardButton(text="🎯 Подсети", callback_data="targets:view")],
        [InlineKeyboardButton(text="🌍 Регионы", callback_data="regions:view"), InlineKeyboardButton(text="✅ Найденные IP", callback_data="found:list")],
        [InlineKeyboardButton(text="⚙️ Настройки поиска", callback_data="scheduler:settings"), InlineKeyboardButton(text="📈 Статистика", callback_data="stats:view")],
        [InlineKeyboardButton(text="🔔 Уведомления", callback_data="notifications:global")],
    ])


def back(callback="home", label="🔙 Назад"):
    return InlineKeyboardMarkup(inline_keyboard=[[InlineKeyboardButton(text=label, callback_data=callback)]])


def accounts(items):
    rows = [[InlineKeyboardButton(text=f"👤 {a.display_name} · {a.scheduler_status}", callback_data=f"account:view:{a.id}")] for a in items]
    rows += [[InlineKeyboardButton(text="➕ Добавить аккаунт", callback_data="account:add")], [InlineKeyboardButton(text="🏠 Главное меню", callback_data="home")]]
    return InlineKeyboardMarkup(inline_keyboard=rows)


def account_actions(account_id, has_topic=False):
    rows = [[InlineKeyboardButton(text="▶️ Запустить / цели", callback_data=f"account:hunt:{account_id}")], [InlineKeyboardButton(text="⚙️ Настройки аккаунта", callback_data=f"account:settings:{account_id}")], [InlineKeyboardButton(text="⏸ Пауза", callback_data=f"account:pause:{account_id}")], [InlineKeyboardButton(text="▶️ Возобновить", callback_data=f"account:resume:{account_id}")], [InlineKeyboardButton(text="🔔 Уведомления", callback_data=f"account:notifications:{account_id}")]]
    if not has_topic:
        rows.append([InlineKeyboardButton(text="🧵 Создать topic", callback_data=f"account:topic:{account_id}")])
    rows += [[InlineKeyboardButton(text="🗑 Удалить", callback_data=f"account:delete:{account_id}")], [InlineKeyboardButton(text="🔙 К аккаунтам", callback_data="account:list")]]
    return InlineKeyboardMarkup(inline_keyboard=rows)


def hunt_accounts(items):
    return InlineKeyboardMarkup(inline_keyboard=[[InlineKeyboardButton(text=f"▶️ {a.display_name}", callback_data=f"hunt:account:{a.id}")] for a in items] + [[InlineKeyboardButton(text="🔙 Назад", callback_data="account:list")]])


def subnets(items, selected):
    rows = [[InlineKeyboardButton(text=("✅ " if x["subnet_id"] in selected else "▫️ ") + f"{x['region']} · {x['cidr']}", callback_data=f"subnet:toggle:{x['subnet_id']}")] for x in items]
    rows += [[InlineKeyboardButton(text="✅ Все", callback_data="subnet:all"), InlineKeyboardButton(text="▫️ Сбросить", callback_data="subnet:none")], [InlineKeyboardButton(text="🚀 Запустить", callback_data="subnet:save")], [InlineKeyboardButton(text="🔙 Назад", callback_data="hunt:back")]]
    return InlineKeyboardMarkup(inline_keyboard=rows)


def notification_settings(account):
    def mark(value): return "✅" if value else "▫️"
    fields = [("notify_account_added", "Аккаунт добавлен"), ("notify_found", "Floating IP найден"), ("notify_no_free_ip", "Нет свободных IP"), ("notify_permission", "Права / авторизация"), ("notify_network", "Сетевые ошибки"), ("notify_rate_limit", "Rate limit"), ("notify_server", "Ошибки 5xx"), ("notify_unknown", "Неизвестные ошибки")]
    rows = [[InlineKeyboardButton(text=f"{mark(getattr(account, field))} {label}", callback_data=f"notify:toggle:{account.id}:{field}")] for field, label in fields]
    rows.append([InlineKeyboardButton(text="🔙 К аккаунту", callback_data=f"account:view:{account.id}")])
    return InlineKeyboardMarkup(inline_keyboard=rows)


def delete_confirmation(account_id):
    return InlineKeyboardMarkup(inline_keyboard=[[InlineKeyboardButton(text="🗑 Да, удалить", callback_data=f"account:delete_yes:{account_id}")], [InlineKeyboardButton(text="Отмена", callback_data=f"account:view:{account_id}")]])


def task_list(items):
    return InlineKeyboardMarkup(inline_keyboard=[[InlineKeyboardButton(text=f"🔄 #{task.id} · {task.subnet_cidr}", callback_data=f"task:view:{task.id}")] for task in items] + [[InlineKeyboardButton(text="🏠 Главное меню", callback_data="home")]])


def task_actions(task_id):
    return InlineKeyboardMarkup(inline_keyboard=[[InlineKeyboardButton(text="⏸ Пауза", callback_data=f"task:pause:{task_id}"), InlineKeyboardButton(text="⏹ Остановить", callback_data=f"task:stop:{task_id}")], [InlineKeyboardButton(text="🔙 К задачам", callback_data="task:list")]])


def scheduler_settings(settings):
    stagger = settings.burst_cooldown
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text=f"⚡ Пауза между запросами: {settings.burst_request_delay:g} сек", callback_data="burst:delay")],
        [InlineKeyboardButton(text=f"🔄 Пауза между циклами: {settings.burst_cooldown} сек", callback_data="burst:cooldown")],
        [InlineKeyboardButton(text=f"🌐 Тайм-аут API: {settings.api_timeout:g} сек", callback_data="burst:timeout")],
        [InlineKeyboardButton(text=f"⚠️ Лимит критических ошибок: {settings.errors_before_disable}", callback_data="burst:errors")],
        [InlineKeyboardButton(text=f"👥 Автораспределение аккаунтов: {stagger} сек", callback_data="burst:noop")],
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
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text=f"{mark} Все уведомления", callback_data="notify:global:toggle")],
        [InlineKeyboardButton(text=f"🧹 Объединять повторы: {settings.aggregate_seconds} сек", callback_data="notify:global:aggregate")],
        [InlineKeyboardButton(text=f"📊 Отчёты: {settings.report_period}", callback_data="notify:global:reports")],
        [InlineKeyboardButton(text="🔙 Назад", callback_data="home")],
    ])


def account_settings_keyboard(account):
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="🌐 Настроить прокси", callback_data=f"account:proxy:{account.id}")],
        [InlineKeyboardButton(text="🔔 Уведомления", callback_data=f"account:notifications:{account.id}")],
        [InlineKeyboardButton(text="📈 Статистика аккаунта", callback_data=f"account:stats:{account.id}")],
        [InlineKeyboardButton(text="🌐 Проверить подключение", callback_data=f"account:check:{account.id}")],
        [InlineKeyboardButton(text="⏸ Приостановить", callback_data=f"account:pause:{account.id}"), InlineKeyboardButton(text="▶️ Возобновить", callback_data=f"account:resume:{account.id}")],
        [InlineKeyboardButton(text="🗑 Удалить аккаунт", callback_data=f"account:delete:{account.id}")],
        [InlineKeyboardButton(text="🔙 К списку аккаунтов", callback_data="account:list")],
    ])


def account_stats_periods(account_id, selected="all"):
    labels = [("1h", "1 час"), ("24h", "24 часа"), ("7d", "7 дней"), ("all", "Всё время")]
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text=("✅ " if value == selected else "") + label, callback_data=f"account:stats:{account_id}:{value}") for value, label in labels],
        [InlineKeyboardButton(text="🔙 К аккаунту", callback_data=f"account:view:{account_id}")],
    ])


# Final account card layout used by all navigation paths.
def account_actions(account_id, has_topic=False):
    rows = [
        [InlineKeyboardButton(text="▶️ Запустить / выбрать цели", callback_data=f"account:hunt:{account_id}")],
        [InlineKeyboardButton(text="⚙️ Настройки аккаунта", callback_data=f"account:settings:{account_id}")],
        [InlineKeyboardButton(text="📈 Статистика", callback_data=f"account:stats:{account_id}")],
        [InlineKeyboardButton(text="🌐 Проверить подключение", callback_data=f"account:check:{account_id}")],
        [InlineKeyboardButton(text="⏸ Приостановить", callback_data=f"account:pause:{account_id}"), InlineKeyboardButton(text="▶️ Возобновить", callback_data=f"account:resume:{account_id}")],
        [InlineKeyboardButton(text="🔔 Уведомления", callback_data=f"account:notifications:{account_id}")],
    ]
    if not has_topic:
        rows.append([InlineKeyboardButton(text="🧵 Создать тему уведомлений", callback_data=f"account:topic:{account_id}")])
    rows += [[InlineKeyboardButton(text="🗑 Удалить аккаунт", callback_data=f"account:delete:{account_id}")], [InlineKeyboardButton(text="🔙 К списку аккаунтов", callback_data="account:list")]]
    return InlineKeyboardMarkup(inline_keyboard=rows)
