from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup


def region_picker(prefix="region"):
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="🌐 ru-3", callback_data=f"{prefix}:ru-3"), InlineKeyboardButton(text="🌐 ru-9", callback_data=f"{prefix}:ru-9")],
        [InlineKeyboardButton(text="🔙 Назад", callback_data="home")],
    ])


def main_menu():
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="➕ Аккаунты", callback_data="account:list"), InlineKeyboardButton(text="▶️ Запустить", callback_data="hunt:start")],
        [InlineKeyboardButton(text="📊 Scheduler", callback_data="scheduler:view"), InlineKeyboardButton(text="🎯 Цели", callback_data="targets:view")],
        [InlineKeyboardButton(text="✅ Найденные IP", callback_data="found:list"), InlineKeyboardButton(text="⚙️ Burst настройки", callback_data="scheduler:settings")],
        [InlineKeyboardButton(text="📈 Статистика", callback_data="stats:view"), InlineKeyboardButton(text="ℹ️ Помощь", callback_data="help")],
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
        [InlineKeyboardButton(text=f"⚡ Запросы: {settings.burst_request_delay:g} сек", callback_data="burst:delay")],
        [InlineKeyboardButton(text=f"🔄 Cooldown: {settings.burst_cooldown} сек", callback_data="burst:cooldown")],
        [InlineKeyboardButton(text=f"🌐 API timeout: {settings.api_timeout:g} сек", callback_data="burst:timeout")],
        [InlineKeyboardButton(text=f"⚠️ Лимит ошибок: {settings.errors_before_disable}", callback_data="burst:errors")],
        [InlineKeyboardButton(text=f"👥 Auto stagger: {stagger} / active", callback_data="burst:noop")],
        [InlineKeyboardButton(text="🔙 Назад", callback_data="home")],
    ])


def account_burst_settings(settings, account_id):
    keyboard = scheduler_settings(settings).inline_keyboard[:-1]
    keyboard.append([InlineKeyboardButton(text="🔔 Уведомления", callback_data=f"account:notifications:{account_id}")])
    keyboard.append([InlineKeyboardButton(text="🔙 К аккаунту", callback_data=f"account:view:{account_id}")])
    return InlineKeyboardMarkup(inline_keyboard=keyboard)
