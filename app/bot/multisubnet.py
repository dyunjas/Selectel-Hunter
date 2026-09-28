from aiogram import BaseMiddleware, F, Router
from aiogram.types import CallbackQuery, Message
from aiogram.fsm.context import FSMContext
from app.config.subnets import BY_ID, TARGET_SUBNETS
from .keyboards import account_actions, hunt_accounts, main_menu, subnets


class AdminOnlyMiddleware(BaseMiddleware):
    def __init__(self, admin_ids): self.admin_ids = admin_ids
    async def __call__(self, handler, event, data):
        user = getattr(event, "from_user", None)
        if user and user.is_bot: return None
        if isinstance(event, CallbackQuery) and event.message and event.message.chat.type != "private": return None
        if user and user.id in self.admin_ids: return await handler(event, data)
        if isinstance(event, CallbackQuery): await event.answer("⛔ Нет доступа", show_alert=True)
        elif isinstance(event, Message): await event.answer("⛔ Нет доступа")


def build_multisubnet_router(repo, manager, admin_ids=None):
    router = Router()
    router.callback_query.outer_middleware(AdminOnlyMiddleware(admin_ids or set()))

    async def account_for(call, account_id):
        account = await repo.get_account(account_id)
        if not account or account.telegram_user_id != call.from_user.id:
            await call.answer("Аккаунт не найден", show_alert=True)
            return None
        return account

    @router.callback_query(F.data.startswith("account:hunt:"))
    async def account_hunt(call, state: FSMContext):
        account = await account_for(call, int(call.data.rsplit(":", 1)[1]))
        if not account: return
        selected = {item["subnet_id"] for item in TARGET_SUBNETS}
        await state.update_data(selected_account=account.id, selected_subnets=list(selected))
        await call.message.edit_text(f"🎯 <b>Новый поиск</b>\n\nАккаунт: <b>{account.display_name}</b>\n\nВсе целевые подсети выбраны автоматически. Можно снять отдельные подсети:", reply_markup=subnets(TARGET_SUBNETS, selected)); await call.answer()

    @router.callback_query(F.data.startswith("hunt:account:"))
    async def hunt_account(call, state: FSMContext):
        account = await account_for(call, int(call.data.rsplit(":", 1)[1]))
        if not account: return
        selected = {item["subnet_id"] for item in TARGET_SUBNETS}
        await state.update_data(selected_account=account.id, selected_subnets=list(selected))
        await call.message.edit_text("🎯 <b>Запуск поиска</b>\n\nВсе целевые подсети выбраны автоматически:", reply_markup=subnets(TARGET_SUBNETS, selected)); await call.answer()

    @router.callback_query(F.data.startswith("subnet:toggle:"))
    async def toggle(call, state: FSMContext):
        data = await state.get_data(); selected = set(data.get("selected_subnets", [])); subnet_id = call.data.rsplit(":", 1)[1]
        if subnet_id in selected: selected.remove(subnet_id)
        else: selected.add(subnet_id)
        await state.update_data(selected_subnets=list(selected)); await call.message.edit_reply_markup(reply_markup=subnets(TARGET_SUBNETS, selected)); await call.answer("Выбор обновлён")

    @router.callback_query(F.data == "subnet:all")
    async def select_all(call, state: FSMContext):
        selected = {item["subnet_id"] for item in TARGET_SUBNETS}; await state.update_data(selected_subnets=list(selected)); await call.message.edit_reply_markup(reply_markup=subnets(TARGET_SUBNETS, selected)); await call.answer("Выбраны все подсети")

    @router.callback_query(F.data == "subnet:none")
    async def select_none(call, state: FSMContext): await state.update_data(selected_subnets=[]); await call.message.edit_reply_markup(reply_markup=subnets(TARGET_SUBNETS, set())); await call.answer("Выбор очищен")

    @router.callback_query(F.data == "subnet:save")
    async def save(call, state: FSMContext):
        data = await state.get_data(); account = await account_for(call, int(data.get("selected_account", 0))); selected = set(data.get("selected_subnets", []))
        if not account or not selected: await call.answer("Выберите хотя бы одну подсеть", show_alert=True); return
        selected_items = [BY_ID[subnet_id] for subnet_id in selected]; await repo.set_subnets(account.id, selected_items); created = []
        for subnet in await repo.enabled_subnets(account.id): created.append(await manager.start_pair(call.from_user.id, account, subnet, "966826e6-d301-4bb5-aa13-77a324d15f0d"))
        await state.clear(); await call.message.edit_text(f"🚀 <b>Поиск запущен</b>\n\nАккаунт: <b>{account.display_name}</b>\nЗадач создано: <b>{len(created)}</b>\n\nКаждая подсеть работает в отдельной задаче.", reply_markup=main_menu()); await call.answer()

    return router
