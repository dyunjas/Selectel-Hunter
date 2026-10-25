from aiogram import BaseMiddleware, F, Router
import json
from aiogram.types import CallbackQuery, Message
from .formatting import safe
from .views import edit_screen
from .presentation import regions_for, region_name, region_keyboard, RULE, keyboard


class AdminOnlyMiddleware(BaseMiddleware):
    def __init__(self, admin_ids, allow_topics=False):
        self.admin_ids = admin_ids
        self.allow_topics = allow_topics

    async def __call__(self, handler, event, data):
        user = getattr(event, "from_user", None)
        if user and user.is_bot:
            return None
        if isinstance(event, CallbackQuery) and event.message and event.message.chat.type != "private" and not self.allow_topics:
            return None
        if isinstance(event, Message) and event.chat.type != "private":
            return None
        if user and user.id in self.admin_ids:
            return await handler(event, data)
        if isinstance(event, CallbackQuery):
            await event.answer("⛔ Нет доступа", show_alert=True)
        elif isinstance(event, Message):
            await event.answer("⛔ Нет доступа")


def build_multisubnet_router(repo, manager, admin_ids=None, client_factory=None):
    router = Router()
    router.callback_query.outer_middleware(AdminOnlyMiddleware(admin_ids or set()))

    async def account_for(call, account_id):
        account = await repo.get_account(account_id)
        if not account or account.telegram_user_id != call.from_user.id:
            await call.answer("Аккаунт не найден", show_alert=True)
            return None
        return account

    async def region_subnets(region):
        return [{"region": t.region, "cidr": t.cidr, "subnet_id": t.subnet_id} for t in await repo.all_targets() if t.region in regions_for(region)]

    async def show_regions(call, state, account):
        await state.update_data(selected_account=account.id, selected_subnets=[])
        pref = await repo.ui.preferences(call.from_user.id) if getattr(repo, "ui", None) else None
        current = pref.selected_region if pref else "spb"
        markup = region_keyboard(current, "huntregion", f"account:view:{account.id}")
        for row in markup.inline_keyboard[:-1]:
            for button in row:
                button.callback_data += f":{account.id}"
        await edit_screen(call.message, f"🌍 <b>ВЫБОР РЕГИОНОВ</b>\n{RULE}\n\nАккаунт: <b>{safe(account.display_name)}</b>\nТекущий выбор: {safe(region_name(current))}\n\nСанкт-Петербург включает все регионы, кроме ru-2.\nВыберите один город или «Оба города» для поиска сразу в Москве и Петербурге:", markup)

    async def draft_screen(call, draft, account, page=0):
        from .views import page_slice
        available = await region_subnets(draft.selection)
        visible, page, pages = page_slice(available, page, 8)
        selected = set(json.loads(draft.selected_json))
        rows = [[(("✅ " if t["subnet_id"] in selected else "▫️ ") + f"{t['region']} · {t['cidr']}", f"s:{draft.id}:toggle:{t['subnet_id']}")] for t in visible]
        if pages > 1:
            rows.append([("‹", f"s:{draft.id}:page:{max(0,page-1)}"), (f"{page+1}/{pages}", "ui:noop"), ("›", f"s:{draft.id}:page:{min(pages-1,page+1)}")])
        rows += [[("✅ Все", f"s:{draft.id}:all"), ("Снять выбор", f"s:{draft.id}:none")],
                 [(f"🎯 Запустить только выбранные · {len(selected)}", f"s:{draft.id}:save")],
                 [("➕ Добавить параллельно", f"s:{draft.id}:add")],
                 [("🔄 Обновить подсети", f"s:{draft.id}:refresh")], [("◀️ К аккаунту", f"account:view:{account.id}")]]
        await edit_screen(call.message, f"🎯 <b>ВЫБОР ПОДСЕТЕЙ</b>\n{RULE}\n\n🌍 {safe(region_name(draft.selection))}\nАккаунт: <b>{safe(account.display_name)}</b>\n\nОбычный запуск оставит в поиске только выбранные подсети этого аккаунта, остальные поставит на паузу.\nДля одновременного поиска используйте «Добавить параллельно».\nВыбрано: <b>{len(selected)}</b> · страница {page+1}/{pages}", keyboard(*rows))

    async def show_selector(call, state, account, region):
        selected = {j.subnet_id for j in await repo.queue.list(account_id=account.id) if j.region in regions_for(region) and j.status in {"pending", "running", "paused"}}
        draft = await repo.ui.create_draft(call.from_user.id, account.id, region, selected)
        await draft_screen(call, draft, account)

    @router.callback_query(F.data.startswith("huntregion:"))
    async def select_region(call, state):
        data = await state.get_data()
        parts = call.data.split(":")
        account_id = int(parts[2]) if len(parts) == 3 and parts[2].isdigit() else int(data.get("selected_account", 0))
        account = await account_for(call, account_id)
        region = call.data.split(":")[1]
        if account and regions_for(region):
            await repo.ui.preferences(call.from_user.id, selected_region=region)
            await show_selector(call, state, account, region)
        await call.answer("Регион выбран")

    @router.callback_query(F.data.regexp(r"^s:[a-f0-9]{12}:(toggle|page|all|none|save|add|refresh)(:[a-f0-9-]+)?$"))
    async def draft_action(call):
        parts = call.data.split(":")
        draft = await repo.ui.draft(parts[1], call.from_user.id)
        if not draft:
            await call.answer("Этот выбор больше недоступен. Откройте поиск заново через /start.", show_alert=True)
            return
        account = await account_for(call, draft.account_id)
        if not account:
            return
        action = parts[2]
        available = await region_subnets(draft.selection)
        allowed = {t["subnet_id"] for t in available}
        selected = set(json.loads(draft.selected_json)) & allowed
        if action == "toggle":
            if len(parts) != 4 or parts[3] not in allowed:
                await call.answer("Подсеть недоступна в выбранном регионе", show_alert=True)
                return
            draft = await repo.ui.draft(draft.id, call.from_user.id, toggle=parts[3])
            selected = set(json.loads(draft.selected_json)) & allowed
        elif action in {"all", "none"}:
            selected = allowed if action == "all" else set()
            await repo.ui.draft(draft.id, call.from_user.id, selected)
        elif action in {"save", "add"}:
            if not selected:
                await call.answer("Выберите хотя бы одну подсеть", show_alert=True)
                return
            enabled = {t.subnet_id for t in await repo.enabled_targets(account.id)}
            if not selected.issubset(enabled):
                await call.answer("Часть подсетей выключена в общих настройках. Включите их или измените выбор. Текущий поиск сохранён.", show_alert=True)
                return
            await repo.queue.enqueue(account, [t for t in available if t["subnet_id"] in selected], exclusive=action == "save")
            await manager.start_account(account.id)
            mode = "Остальные задания этого аккаунта поставлены на паузу." if action == "save" else "Другие задания продолжают поиск параллельно."
            await edit_screen(call.message, f"🟢 <b>ПОИСК ЗАПУЩЕН</b>\n{RULE}\n\n{safe(region_name(draft.selection))}\nАккаунт: <b>{safe(account.display_name)}</b>\nПодсетей: <b>{len(selected)}</b>\n\n{mode}\nСтатус и управление — в разделе «Мои задания».", keyboard([("📋 Мои задания", "task:list")], [("◀️ Главное меню", "home")]))
            await call.answer("Задания сохранены")
            return
        elif action == "refresh":
            try:
                client = await client_factory(account.id)
                for region in regions_for(draft.selection):
                    pools = (await client.get_floatingip_pools(region)).get("floatingip_pools", [])
                    await repo.refresh_pools(region, pools)
            except Exception:
                await call.answer("Не удалось обновить список. Сохранённые подсети доступны.", show_alert=True)
                return
        draft = await repo.ui.draft(draft.id, call.from_user.id)
        await draft_screen(call, draft, account, int(parts[3]) if action == "page" and len(parts) == 4 else 0)
        await call.answer()

    @router.callback_query(F.data.startswith("account:hunt:"))
    async def account_hunt(call, state):
        account = await account_for(call, int(call.data.rsplit(":", 1)[1]))
        if account:
            await show_regions(call, state, account)
            await call.answer()

    @router.callback_query(F.data.startswith("hunt:account:"))
    async def hunt_account(call, state):
        account = await account_for(call, int(call.data.rsplit(":", 1)[1]))
        if account:
            await show_regions(call, state, account)
            await call.answer()

    @router.callback_query(F.data.startswith("subnet:"))
    async def legacy_selection(call):
        await call.answer("Откройте выбор подсетей заново через /start — настройки и задания сохранены.", show_alert=True)

    return router
