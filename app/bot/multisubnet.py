from aiogram import BaseMiddleware, F, Router
from aiogram.types import CallbackQuery, Message
from aiogram.fsm.context import FSMContext
from app.config.subnets import BY_ID, TARGET_SUBNETS
from .keyboards import account_return, subnets
from .formatting import safe


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

    def region_subnets(region=None):
        return list(TARGET_SUBNETS)

    async def show_selector(call, state, account):
        available = region_subnets()
        selected = {item.subnet_id for item in await repo.all_targets() if item.enabled}
        await state.update_data(selected_account=account.id, selected_subnets=list(selected))
        await call.message.edit_text(
            f"🎯 <b>Подсети для поиска</b>\n\nЗапускаем: <b>{safe(account.display_name)}</b>\n\n✅ — выбрана · ▫️ — выключена\nВыбор подсетей применяется ко всем аккаунтам. Аккаунт проверит подсети во включённых регионах.\n\nВыберите подсети, затем нажмите «Сохранить и запустить».",
            reply_markup=subnets(available, selected, f"account:view:{account.id}"),
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
        account = await account_for(call, int(data.get("selected_account", 0)))
        if not account:
            return
        selected = set(data.get("selected_subnets", []))
        subnet_id = call.data.rsplit(":", 1)[1]
        if subnet_id in selected:
            selected.remove(subnet_id)
        else:
            selected.add(subnet_id)
        await state.update_data(selected_subnets=list(selected))
        await call.message.edit_reply_markup(reply_markup=subnets(region_subnets(), selected, f"account:view:{account.id}"))
        await call.answer("Выбор обновлён")

    @router.callback_query(F.data == "subnet:all")
    async def select_all(call, state):
        data = await state.get_data()
        account = await account_for(call, int(data.get("selected_account", 0)))
        if not account:
            return
        selected = {item["subnet_id"] for item in region_subnets()}
        await state.update_data(selected_subnets=list(selected))
        await call.message.edit_reply_markup(reply_markup=subnets(region_subnets(), selected, f"account:view:{account.id}"))
        await call.answer("Выбраны все подсети")

    @router.callback_query(F.data == "subnet:none")
    async def select_none(call, state):
        data = await state.get_data()
        account = await account_for(call, int(data.get("selected_account", 0)))
        if not account:
            return
        await state.update_data(selected_subnets=[])
        await call.message.edit_reply_markup(reply_markup=subnets(region_subnets(), set(), f"account:view:{account.id}"))
        await call.answer("Выбор очищен")

    @router.callback_query(F.data == "subnet:save")
    async def save(call, state):
        data = await state.get_data()
        account = await account_for(call, int(data.get("selected_account", 0)))
        selected = set(data.get("selected_subnets", []))
        if not account:
            return
        if not selected:
            await call.answer("Выберите хотя бы одну подсеть", show_alert=True)
            return
        selected_items = [BY_ID[item] for item in selected]
        await repo.set_targets_enabled(selected)
        await manager.start_account(account.id)
        await state.clear()
        await call.message.edit_text(
            f"🚀 <b>Поиск запущен</b>\n\nАккаунт: <b>{safe(account.display_name)}</b>\nВыбрано подсетей: <b>{len(selected_items)}</b>\n\nБлижайший старт — в расписании.",
            reply_markup=account_return(account.id),
        )
        await call.answer()

    return router
