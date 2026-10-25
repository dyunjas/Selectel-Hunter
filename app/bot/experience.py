from aiogram import Router, F
from aiogram.filters import Command, CommandStart
from .multisubnet import AdminOnlyMiddleware
from .views import edit_screen, HELP_TEXT
from .presentation import keyboard, region_keyboard, region_name, safe, RULE
from .screens import UiScreens
from app.selectel.errors import SelectelError


def build_experience_router(repo, manager, admin_ids, client_factory, screens=None):
    router = Router()
    guard = AdminOnlyMiddleware(admin_ids)
    router.message.outer_middleware(guard)
    router.callback_query.outer_middleware(AdminOnlyMiddleware(admin_ids, allow_topics=True))
    screens = screens or UiScreens(repo, manager)

    @router.message(CommandStart())
    async def start(message, state):
        await state.clear()
        text, markup = await screens.dashboard(message.from_user.id)
        await message.answer(text, reply_markup=markup)

    @router.callback_query(F.data == "home")
    async def home(call, state):
        await state.clear()
        await edit_screen(call.message, *(await screens.dashboard(call.from_user.id)))
        await call.answer()

    @router.message(Command("status"))
    async def status(message):
        text, markup = await screens.listing(message.from_user.id, active_only=True)
        await message.answer(text, reply_markup=markup)

    @router.message(Command("stats"))
    async def stats_command(message):
        text, markup = await screens.stats(message.from_user.id)
        await message.answer(text, reply_markup=markup)

    @router.message(Command("help"))
    async def help_command(message):
        await message.answer(HELP_TEXT + "\n/stats — статистика.\nМосква — ru-2; Санкт-Петербург — все остальные регионы.\nЧасовой пояс и частота живой карточки — в настройках.", reply_markup=keyboard([("◀️ Главное меню", "home")]))

    @router.callback_query(F.data == "ui:regions")
    async def regions(call):
        pref = await repo.ui.preferences(call.from_user.id)
        await edit_screen(call.message, f"🌍 <b>ВЫБОР РЕГИОНОВ</b>\n{RULE}\n\nВыберите один город или «Оба города» для одновременного поиска в Москве и Петербурге.\nСанкт-Петербург включает все регионы, кроме ru-2.\n\n✅ Текущий выбор: <b>{safe(region_name(pref.selected_region))}</b>\n\nВыбор действует для нового поиска. Существующие задания сохраняют свой регион.", region_keyboard(pref.selected_region))
        await call.answer()

    @router.callback_query(F.data.regexp(r"^city:(ru-2|spb|all)$"))
    async def city(call):
        selected = call.data.split(":")[1]
        await repo.ui.preferences(call.from_user.id, selected_region=selected)
        cities = await repo.ui.active_by_city(call.from_user.id)
        await edit_screen(call.message, f"✅ <b>РЕГИОНЫ ВЫБРАНЫ ДЛЯ ЗАПУСКА</b>\n{RULE}\n\n{safe(region_name(selected))}\n\nСейчас ищут: Москва — {cities['ru-2']}, Петербург — {cities['spb']}.\nНажмите «Запустить выбранные регионы», чтобы применить выбор ко всем аккаунтам. Задания вне выбранных регионов будут на паузе; история и IP сохранятся.",
            keyboard([("🚀 Запустить выбранные регионы", "hunt:all")], [("➕ Добавить параллельно", f"hunt:add:{selected}")], [("🎯 Выбрать аккаунт и подсети", "hunt:start")], [("🌍 Выбрать регионы", "ui:regions")], [("◀️ Главное меню", "home")]))
        await call.answer("Регион сохранён")

    @router.callback_query(F.data == "stats:view")
    @router.callback_query(F.data.regexp(r"^ui:stats:(all|spb|ru-2)$"))
    async def stats(call):
        region = call.data.rsplit(":", 1)[1] if call.data.startswith("ui:stats:") else "all"
        await edit_screen(call.message, *(await screens.stats(call.from_user.id, region)))
        await call.answer()

    @router.callback_query(F.data == "ui:settings")
    @router.callback_query(F.data.regexp(r"^ui:(refresh|timezone):-?\d+$"))
    async def settings(call):
        if call.data != "ui:settings":
            _, field, value = call.data.split(":")
            try:
                await repo.ui.preferences(call.from_user.id, **{"refresh_seconds" if field == "refresh" else "utc_offset_minutes": int(value)})
            except ValueError:
                await call.answer("Недопустимое значение настройки", show_alert=True)
                return
        await edit_screen(call.message, *(await screens.settings(call.from_user.id)))
        await call.answer()

    @router.callback_query(F.data == "found:list")
    @router.callback_query(F.data.regexp(r"^found:page:\d+$"))
    @router.callback_query(F.data.regexp(r"^history:(region:(all|spb|ru-2)|job:[a-f0-9]{32}):\d+$"))
    async def history(call):
        region, job_id, page = "all", None, 0
        if call.data.startswith("history:"):
            _, scope, value, page = call.data.split(":")
            region, job_id = (value, None) if scope == "region" else ("all", value)
        elif call.data.startswith("found:page:"):
            page = call.data.rsplit(":", 1)[1]
        await edit_screen(call.message, *(await screens.history(call.from_user.id, region, job_id, int(page))))
        await call.answer()

    @router.callback_query(F.data.regexp(r"^ip:(view|check):\d+$"))
    async def ip(call):
        _, action, found_id = call.data.split(":")
        item = await repo.ui.found_item(call.from_user.id, found_id=int(found_id))
        if not item:
            await call.answer("IP не найден или больше недоступен", show_alert=True)
            return
        if action == "check":
            try:
                account = await repo.get_account(item.account_id)
                client = await client_factory(item.account_id)
                resource = await client.get_floating_ip(item.floating_ip_id, item.region)
                if resource.get("id") != item.floating_ip_id or (resource.get("project_id") or resource.get("tenant_id")) != account.project_id:
                    raise ValueError("Ownership mismatch")
                await repo.ui.observation(item.id, resource.get("status") or "Получен из API, статус не передан")
            except SelectelError as exc:
                if exc.status == 404:
                    await repo.ui.observation(item.id, "Отсутствует (404)")
                else:
                    await call.answer("Сейчас не удалось проверить Selectel. Предыдущее подтверждение сохранено.", show_alert=True)
                    return
            except Exception:
                await call.answer("Не удалось подтвердить состояние ресурса", show_alert=True)
                return
        await edit_screen(call.message, *(await screens.ip(call.from_user.id, item.id)))
        await call.answer()

    @router.callback_query(F.data == "ui:noop")
    async def noop(call):
        await call.answer()

    @router.callback_query(F.data.regexp(r"^task:(view|pause|stop|resume):\d+$"))
    async def legacy_task(call):
        task = await repo.get_task(int(call.data.rsplit(":", 1)[1]))
        if not task or task.telegram_user_id != call.from_user.id:
            await call.answer("Задание не найдено. Откройте «Мои задания» через /start.", show_alert=True)
            return
        jobs = await repo.queue.list(account_id=task.account_id)
        job = next((j for j in jobs if j.region == task.region and j.subnet_id == task.subnet_id), None)
        if job:
            await edit_screen(call.message, *(await screens.job(call.from_user.id, job.id)))
        else:
            item = await repo.ui.found_item(call.from_user.id, task_id=task.id)
            if item:
                await edit_screen(call.message, *(await screens.ip(call.from_user.id, item.id)))
            else:
                await edit_screen(call.message, "📋 <b>АРХИВНОЕ ЗАДАНИЕ</b>\n\nИстория сохранена. Управление текущим поиском доступно в разделе «Мои задания».", keyboard([("📋 Мои задания", "task:list")], [("◀️ Главное меню", "home")]))
        await call.answer("Используйте актуальные кнопки карточки задания")
    return router
