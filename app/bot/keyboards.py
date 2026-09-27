from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup


def main_menu():
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="➕ Добавить аккаунт", callback_data="account:add")],
        [InlineKeyboardButton(text="👤 Мои аккаунты", callback_data="account:list")],
        [InlineKeyboardButton(text="🎯 Запустить поиск", callback_data="hunt:start")],
        [InlineKeyboardButton(text="📋 Активные задачи", callback_data="task:list")],
        [InlineKeyboardButton(text="✅ Найденные IP", callback_data="found:list")],
    ])


def back_menu():
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="🏠 Главное меню", callback_data="home")],
    ])


def account_actions(account_id, has_topic=False):
    rows = [
        [InlineKeyboardButton(text="✏️ Редактировать", callback_data=f"account:edit:{account_id}")],
        [InlineKeyboardButton(text="🔔 Уведомления", callback_data=f"account:notifications:{account_id}")],
        [InlineKeyboardButton(text="🗑 Удалить аккаунт", callback_data=f"account:delete:{account_id}")],
        [InlineKeyboardButton(text="🔙 К списку аккаунтов", callback_data="account:list")],
    ]
    if not has_topic: rows.insert(1, [InlineKeyboardButton(text="🧵 Создать topic", callback_data=f"account:topic:{account_id}")])
    return InlineKeyboardMarkup(inline_keyboard=rows)


def notification_settings(account):
    mark = lambda enabled: "✅ Вкл" if enabled else "❌ Выкл"
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text=f"Аккаунт добавлен: {mark(account.notify_account_added)}", callback_data=f"notify:toggle:{account.id}:notify_account_added")],
        [InlineKeyboardButton(text=f"Ошибки и повторы: {mark(account.notify_errors)}", callback_data=f"notify:toggle:{account.id}:notify_errors")],
        [InlineKeyboardButton(text=f"Найденный IP: {mark(account.notify_found)}", callback_data=f"notify:toggle:{account.id}:notify_found")],
        [InlineKeyboardButton(text="🔙 К аккаунту", callback_data=f"account:view:{account.id}")],
    ])


def delete_confirmation(account_id):
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="🗑 Да, удалить", callback_data=f"account:delete_yes:{account_id}")],
        [InlineKeyboardButton(text="🔙 Отмена", callback_data=f"account:view:{account_id}")],
    ])


def task_actions(task_id):
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="⏸ Пауза", callback_data=f"task:pause:{task_id}"), InlineKeyboardButton(text="⏹ Остановить", callback_data=f"task:stop:{task_id}")],
        [InlineKeyboardButton(text="🏠 Главное меню", callback_data="home")],
    ])


def task_list_keyboard(items):
    rows = [[InlineKeyboardButton(text=f"🔄 #{task.id} · {task.subnet_cidr}", callback_data=f"task:view:{task.id}")] for task in items]
    rows.append([InlineKeyboardButton(text="🏠 Главное меню", callback_data="home")])
    return InlineKeyboardMarkup(inline_keyboard=rows)


def accounts(items):
    rows = [[InlineKeyboardButton(text=f"👤 {a.display_name}", callback_data=f"account:view:{a.id}")] for a in items]
    rows.append([InlineKeyboardButton(text="➕ Добавить аккаунт", callback_data="account:add")])
    rows.append([InlineKeyboardButton(text="🏠 Главное меню", callback_data="home")])
    return InlineKeyboardMarkup(inline_keyboard=rows)


def hunt_accounts(items):
    rows = [[InlineKeyboardButton(text=f"▶️ {a.display_name}", callback_data=f"hunt:account:{a.id}")] for a in items]
    rows.append([InlineKeyboardButton(text="🏠 Главное меню", callback_data="home")])
    return InlineKeyboardMarkup(inline_keyboard=rows)


def subnets(items, selected):
    rows = [[InlineKeyboardButton(text=("✅ " if x["subnet_id"] in selected else "⬜ ") + x["cidr"], callback_data=f"subnet:toggle:{x['subnet_id']}")] for x in items]
    rows.append([InlineKeyboardButton(text="✖️ Очистить выбор", callback_data="subnet:none")])
    rows.append([InlineKeyboardButton(text="🚀 Запустить поиск", callback_data="subnet:save")])
    rows.append([InlineKeyboardButton(text="🔙 Назад", callback_data="hunt:start")])
    return InlineKeyboardMarkup(inline_keyboard=rows)
