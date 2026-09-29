from aiogram import BaseMiddleware, F, Router
from aiogram.types import CallbackQuery, Message
from aiogram.fsm.context import FSMContext
from app.config.subnets import BY_ID, TARGET_SUBNETS
from .keyboards import main_menu, subnets


class AdminOnlyMiddleware(BaseMiddleware):
    def __init__(self, admin_ids):
        self.admin_ids = admin_ids

    async def __call__(self, handler, event, data):
        user = getattr(event, "from_user", None)
        if user and user.is_bot:
            return None
        if isinstance(event, CallbackQuery) and event.message and event.message.chat.type != "private":
            return None
        if user and user.id in self.admin_ids:
            return await handler(event, data)
        if isinstance(event, CallbackQuery):
            await event.answer("⛔ Нет доступа", show_alert=True)
        elif isinstance(event, Message):
            await event.answer("⛔ Нет доступа")


def build_multisubnet_router(repo, manager, admin_ids=None):
    router = Router()
    router.callback_query.outer_middleware(AdminOnlyMiddleware(admin_ids or set()))

    async def account_for(call, account_id):
        account = await repo.get_account(account_id)
        if not account or account.telegram_user_id != call.from_user.id:
            await call.answer("Аккаунт не найден", show_alert=True)
            return None
        return account

    def region_subnets(region):
        return [item for item in TARGET_SUBNETS if item["region"] == region]

    async def show_selector(call, state, account):
        available = region_subnets(account.region)
        selected = {item["subnet_id"] for item in available}
        await state.update_data(selected_account=account.id, selected_subnets=list(selected))
        await call.message.edit_text(
            f"🎯 <b>Целевые подсети</b>\n\nАккаунт: <b>{account.display_name}</b>\nРегион: <code>{account.region}</code>\n\nВыберите одну или несколько подсетей:",
            reply_markup=subnets(available, selected),
        )

    @router.callback_query(F.data.startswith("account:hunt:"))
    async def account_hunt(call, state):
        account = await account_for(call, int(call.data.rsplit(":", 1)[1]))
        if account:
            await show_selector(call, state, account)
            await call.answer()

    @router.callback_query(F.data.startswith("hunt:account:"))
    async def hunt_account(call, state):
        account = await account_for(call, int(call.data.rsplit(":", 1)[1]))
        if account:
            await show_selector(call, state, account)
            await call.answer()

    @router.callback_query(F.data.startswith("subnet:toggle:"))
    async def toggle(call, state):
        data = await state.get_data()
        selected = set(data.get("selected_subnets", []))
        subnet_id = call.data.rsplit(":", 1)[1]
        if subnet_id in selected:
            selected.remove(subnet_id)
        else:
            selected.add(subnet_id)
        await state.update_data(selected_subnets=list(selected))
        account = await repo.get_account(data.get("selected_account"))
        await call.message.edit_reply_markup(reply_markup=subnets(region_subnets(account.region), selected))
        await call.answer("Выбор обновлён")

    @router.callback_query(F.data == "subnet:all")
    async def select_all(call, state):
        data = await state.get_data()
        account = await repo.get_account(data.get("selected_account"))
        selected = {item["subnet_id"] for item in region_subnets(account.region)}
        await state.update_data(selected_subnets=list(selected))
        await call.message.edit_reply_markup(reply_markup=subnets(region_subnets(account.region), selected))
        await call.answer("Выбраны все подсети")

    @router.callback_query(F.data == "subnet:none")
    async def select_none(call, state):
        data = await state.get_data()
        account = await repo.get_account(data.get("selected_account"))
        await state.update_data(selected_subnets=[])
        await call.message.edit_reply_markup(reply_markup=subnets(region_subnets(account.region), set()))
        await call.answer("Выбор очищен")

    @router.callback_query(F.data == "subnet:save")
    async def save(call, state):
        data = await state.get_data()
        account = await account_for(call, int(data.get("selected_account", 0)))
        selected = set(data.get("selected_subnets", []))
        if not account or not selected:
            await call.answer("Выберите хотя бы одну подсеть", show_alert=True)
            return
        selected_items = [BY_ID[item] for item in selected if BY_ID[item]["region"] == account.region]
        await repo.set_subnets(account.id, selected_items)
        await manager.start_account(account.id)
        await state.clear()
        await call.message.edit_text(
            f"🚀 <b>Поиск запущен</b>\n\nАккаунт: <b>{account.display_name}</b>\nРегион: <code>{account.region}</code>\nПодсетей в очереди: <b>{len(selected_items)}</b>\n\nЗапросы выполняются последовательно с интервалом аккаунта.",
            reply_markup=main_menu(),
        )
        await call.answer()

    return router
