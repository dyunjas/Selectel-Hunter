import logging
from datetime import datetime, timedelta
from types import SimpleNamespace
from aiogram import BaseMiddleware, F, Router
from aiogram.filters import Command, CommandStart, StateFilter
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.types import CallbackQuery, Message
from app.config.subnets import BY_ID, TARGET_SUBNETS
from app.config.regions import REGIONS
from .keyboards import account_actions, account_stats_periods, accounts, back, delete_confirmation, found_pages, hunt_accounts, main_menu, notification_settings, region_picker, subnets, task_actions, task_list as task_list_keyboard, wizard_controls
from .formatting import safe
from .views import HELP_TEXT, edit_screen, local_time, overview, page_slice, status_label

back_menu = back

log = logging.getLogger(__name__)


class AdminOnlyMiddleware(BaseMiddleware):
    def __init__(self, admin_ids): self.admin_ids = admin_ids
    async def __call__(self, handler, event, data):
        user = getattr(event, "from_user", None)
        if user and user.is_bot: return None
        if isinstance(event, Message) and event.chat.type != "private": return None
        if user and user.id in self.admin_ids: return await handler(event, data)
        if isinstance(event, CallbackQuery): await event.answer("⛔ Нет доступа", show_alert=True)
        elif isinstance(event, Message): await event.answer("⛔ Нет доступа")


class AddAccount(StatesGroup):
    name = State(); domain = State(); username = State(); password = State(); project = State(); region = State(); proxy = State(); min_interval = State(); max_interval = State()


class EditAccount(StatesGroup):
    name = State(); domain = State(); username = State(); password = State(); project = State(); region = State(); proxy = State(); min_interval = State(); max_interval = State()


class AccountProxy(StatesGroup):
    value = State()


def build_router(repo, manager, secret_box, client_factory, admin_ids=None, bot=None, notification_chat_id=0):
    router = Router(); guard = AdminOnlyMiddleware(admin_ids or set()); router.message.outer_middleware(guard); router.callback_query.outer_middleware(guard)
    chosen_account = {}; chosen_subnet = {}

    @router.message(CommandStart())
    async def start(message, state: FSMContext):
        await state.clear()
        await message.answer(await overview(repo, manager, message.from_user.id), reply_markup=main_menu())
    @router.message(Command("help"))
    async def help_command(message): await message.answer(HELP_TEXT, reply_markup=back())
    @router.message(Command("cancel"))
    async def cancel(message, state): await state.clear(); await message.answer("↩️ Текущее действие отменено.", reply_markup=main_menu())
    @router.message(Command("status"))
    async def status(message):
        await message.answer(await overview(repo, manager, message.from_user.id), reply_markup=main_menu())
    @router.callback_query(F.data == "home")
    async def home(call, state: FSMContext):
        await state.clear()
        await edit_screen(call.message, await overview(repo, manager, call.from_user.id), main_menu())
        await call.answer()
    @router.callback_query(F.data == "help")
    async def help_screen(call):
        await edit_screen(call.message, HELP_TEXT, back())
        await call.answer()

    @router.callback_query(F.data == "account:add")
    async def account_add_region_prompt(call, state):
        await state.clear()
        await state.set_state(AddAccount.name)
        await call.message.edit_text("➕ <b>Добавить аккаунт · 1/6</b>\n\nВведите название, по которому вы узнаете аккаунт:", reply_markup=wizard_controls())
        await call.answer()

    @router.callback_query(F.data == "account:add:region")
    async def account_add(call, state): await account_add_region_prompt(call, state)
    @router.message(AddAccount.name, F.text)
    async def account_name(message, state): await state.update_data(name=message.text); await state.set_state(AddAccount.domain); await message.answer("➕ <b>Добавить аккаунт · 2/6</b>\n\nВведите Account / Domain из панели Selectel:", reply_markup=wizard_controls())
    @router.message(AddAccount.domain, F.text)
    async def account_domain(message, state): await state.update_data(domain=message.text); await state.set_state(AddAccount.username); await message.answer("➕ <b>Добавить аккаунт · 3/6</b>\n\nВведите имя пользователя Selectel:", reply_markup=wizard_controls())
    @router.message(AddAccount.username, F.text)
    async def account_username(message, state): await state.update_data(username=message.text); await state.set_state(AddAccount.password); await message.answer("➕ <b>Добавить аккаунт · 4/6</b>\n\nВведите пароль Selectel:", reply_markup=wizard_controls())
    @router.message(AddAccount.password, F.text)
    async def account_password(message, state): await state.update_data(password=message.text); await state.set_state(AddAccount.project); await message.answer("➕ <b>Добавить аккаунт · 5/6</b>\n\nВведите ID или название проекта:", reply_markup=wizard_controls())
    @router.message(AddAccount.project, F.text)
    async def account_project_global(message, state):
        await state.update_data(project=message.text, region="ru-3")
        await state.set_state(AddAccount.proxy)
        await message.answer("➕ <b>Добавить аккаунт · 6/6</b>\n\nОтправьте адрес HTTP/SOCKS5-прокси:\n<code>http://user:pass@host:port</code>\n<code>socks5://user:pass@host:port</code>\n\nИли нажмите «Без прокси». Регионы и период поиска задаются в общих настройках.", reply_markup=wizard_controls(skip_proxy=True))
    @router.message(AddAccount.proxy, F.text)
    async def account_proxy_burst_defaults(message, state):
        """Finish account creation with the global burst defaults."""
        value = message.text.strip()
        if value != "-" and not value.startswith(("http://", "https://", "socks5://")):
            await message.answer("Укажите адрес с протоколом http://, https:// или socks5://, либо нажмите «Без прокси».", reply_markup=wizard_controls(skip_proxy=True))
            return
        await state.update_data(
            proxy=None if message.text.strip() == "-" else message.text.strip(),
            min_interval=30,
            max_interval=60,
        )
        await account_max(
            SimpleNamespace(
                text="60",
                from_user=message.from_user,
                answer=message.answer,
            ),
            state,
        )

    @router.callback_query(AddAccount.proxy, F.data == "account:add:skip_proxy")
    async def skip_account_proxy(call, state):
        await call.answer("Сохраняю аккаунт…")
        await account_proxy_burst_defaults(SimpleNamespace(text="-", from_user=call.from_user, answer=call.message.answer), state)

    @router.message(StateFilter(AddAccount), ~F.text)
    async def account_add_text_required(message):
        await message.answer("На этом шаге нужен текст. Отправьте данные сообщением или отмените добавление.", reply_markup=wizard_controls())

    @router.message(AddAccount.max_interval)
    async def account_max(message, state):
        try: maximum = int(message.text)
        except ValueError: await message.answer("Введите целое число, например 10:"); return
        data = await state.get_data(); region = data["region"]; maximum = max(data["min_interval"], maximum)
        account = await repo.add_account(telegram_user_id=message.from_user.id, display_name=data["name"], domain=data["domain"], username=data["username"], encrypted_password=secret_box.encrypt(data["password"]), encrypted_proxy_url=secret_box.encrypt(data["proxy"]) if data.get("proxy") else None, project_id=data["project"], project_name=data["project"], region=region, min_interval=data["min_interval"], max_interval=maximum, auth_url="https://cloud.api.selcloud.ru/identity/v3/auth/tokens", network_api_url=f"https://{region}.cloud.api.selcloud.ru/network/v2.0")
        topic_ok = False
        if notification_chat_id and bot:
            try:
                chat = await bot.get_chat(notification_chat_id)
                if chat.type != "supergroup": raise RuntimeError(f"chat имеет тип {chat.type}, нужна супергруппа")
                if chat.is_forum is False: raise RuntimeError("в супергруппе выключены Topics")
                topic = await bot.create_forum_topic(chat_id=notification_chat_id, name=account.display_name)
                await repo.update_account(account.id, topic_chat_id=notification_chat_id, topic_thread_id=topic.message_thread_id)
                notification_options = await repo.notification_settings()
                if account.notifications_enabled and notification_options.enabled and account.notify_account_added:
                    await bot.send_message(notification_chat_id, f"✅ Аккаунт добавлен\n{safe(account.display_name)}\nBurst-планировщик активирован.", message_thread_id=topic.message_thread_id)
                topic_ok = True
            except Exception as exc:
                topic_ok = False; log.exception("failed to create Telegram topic", extra={"account_id": account.id, "chat_id": notification_chat_id})
                await message.answer(f"⚠️ Аккаунт сохранён, но topic не создан.\nПричина: {type(exc).__name__}: {exc}")
        await state.clear(); await message.answer(f"✅ <b>Аккаунт сохранён</b>\n\nНазвание: {safe(account.display_name)}\nПрокси: {'включён' if data.get('proxy') else 'не используется'}\nТема уведомлений: {'создана' if topic_ok else 'не настроена'}\n\nТеперь можно выбрать подсети и запустить поиск.", reply_markup=account_actions(account.id, topic_ok, account.scheduler_status))

    @router.callback_query(F.data == "keyboard:noop")
    async def keyboard_noop(call):
        await call.answer()

    @router.callback_query(F.data == "account:list")
    @router.callback_query(F.data.regexp(r"^account:page:\d+$"))
    async def account_list(call):
        page = int(call.data.rsplit(":", 1)[1]) if call.data.startswith("account:page:") else 0
        items = await repo.accounts(call.from_user.id)
        active = sum(account.scheduler_status in {"RUNNING", "RATE_LIMIT_COOLDOWN"} for account in items)
        text = f"👤 <b>Аккаунты · {len(items)}</b>\n\nВ поиске: <b>{active}</b> · остальные: <b>{len(items) - active}</b>\n\nОткройте карточку, чтобы управлять поиском и подключением." if items else "👤 <b>Аккаунты</b>\n\nДобавьте первый аккаунт кнопкой ниже."
        await edit_screen(call.message, text, accounts(items, page)); await call.answer()
    @router.callback_query(F.data.startswith("account:view:"))
    async def account_view(call):
        account = await repo.get_account(int(call.data.rsplit(":", 1)[1]))
        if not account or account.telegram_user_id != call.from_user.id: await call.answer("Аккаунт не найден", show_alert=True); return
        stats = await repo.account_attempt_stats(account.id)
        targets = await repo.enabled_targets(account.id)
        next_burst = local_time(account.next_cycle_at)
        await edit_screen(call.message,
            f"👤 <b>{safe(account.display_name)}</b>\n\n"
            f"{status_label(account.scheduler_status)}\n"
            f"Следующий BURST: <b>{next_burst}</b> · ЕКБ (UTC+5)\n"
            f"Прокси: <b>{'✅ подключён' if account.encrypted_proxy_url else '❌ не настроен'}</b>\n"
            f"Целей в очереди: <b>{len(targets)}</b>\n\n"
            "<b>Краткая статистика</b>\n"
            f"Запросов: <b>{sum(stats.values())}</b>\n"
            f"Найдено IP: <b>{stats.get('FOUND', 0)}</b>\n"
            f"Ошибок сети: <b>{stats.get('NETWORK_ERROR', 0)}</b>\n"
            f"Нет свободных IP: <b>{stats.get('NO_FREE_IP', 0)}</b>",
            account_actions(account.id, bool(account.topic_thread_id), account.scheduler_status),
        )
        await call.answer()

    @router.callback_query(F.data.startswith("account:check:"))
    async def account_check(call):
        account = await repo.get_account(int(call.data.rsplit(":", 1)[1]))
        if not account or account.telegram_user_id != call.from_user.id:
            await call.answer("Аккаунт не найден", show_alert=True)
            return
        await call.answer("Проверяю подключение…")
        try:
            client = await client_factory(account.id)
            await client.validate_account()
            result = "✅ Подключение работает. Авторизация и API доступны."
        except Exception as exc:
            result = f"❌ Проверка не пройдена.\n\n<code>{type(exc).__name__}: {exc}</code>"
        await call.message.edit_text(
            f"🌐 <b>Проверка подключения</b>\n\nАккаунт: <b>{safe(account.display_name)}</b>\n\n{result}",
            reply_markup=account_actions(account.id, bool(account.topic_thread_id), account.scheduler_status),
        )

    @router.callback_query(F.data.startswith("account:stats:"))
    async def account_stats(call):
        parts = call.data.split(":")
        account = await repo.get_account(int(parts[2]))
        if not account or account.telegram_user_id != call.from_user.id:
            await call.answer("Аккаунт не найден", show_alert=True)
            return
        period = parts[3] if len(parts) > 3 else "all"
        since = {"1h": datetime.utcnow() - timedelta(hours=1), "24h": datetime.utcnow() - timedelta(days=1), "7d": datetime.utcnow() - timedelta(days=7)}.get(period)
        values = await repo.account_statistics(account.id, since)
        by_result = values["by_result"]
        await call.message.edit_text(
            f"📈 <b>Статистика аккаунта</b>\n\n"
            f"👤 {safe(account.display_name)}\n"
            f"Запросов: <b>{values['total']}</b>\n"
            f"Найдено IP: <b>{by_result.get('FOUND', {}).get('count', 0)}</b>\n"
            f"Нет свободных IP: <b>{by_result.get('NO_FREE_IP', {}).get('count', 0)}</b>\n"
            f"Сетевые ошибки: <b>{by_result.get('NETWORK_ERROR', {}).get('count', 0)}</b>\n"
            f"Ошибки доступа: <b>{by_result.get('PERMISSION_ERROR', {}).get('count', 0) + by_result.get('AUTH_ERROR', {}).get('count', 0)}</b>\n"
            f"Ограничения API: <b>{by_result.get('RATE_LIMIT', {}).get('count', 0)}</b>\n"
            f"Средний ответ API: <b>{values['average_ms'] / 1000:.2f} сек.</b>",
            reply_markup=account_stats_periods(account.id, period),
        )
        await call.answer()
    @router.callback_query(F.data.startswith("account:hunt:"))
    async def account_hunt(call):
        account_id = int(call.data.rsplit(":", 1)[1]); account = await repo.get_account(account_id)
        if not account or account.telegram_user_id != call.from_user.id: await call.answer("Аккаунт не найден", show_alert=True); return
        chosen_account[call.from_user.id] = account_id; chosen_subnet[call.from_user.id] = set(); await call.message.edit_text(f"🎯 <b>Новый поиск</b>\n\nАккаунт: <b>{safe(account.display_name)}</b>\nВыберите одну подсеть:", reply_markup=subnets(TARGET_SUBNETS, set())); await call.answer()
    @router.callback_query(F.data.startswith("account:notifications:"))
    async def account_notifications(call):
        account = await repo.get_account(int(call.data.rsplit(":", 1)[1]))
        if not account or account.telegram_user_id != call.from_user.id: await call.answer("Аккаунт не найден", show_alert=True); return
        await call.message.edit_text(f"🔔 Уведомления\n\nАккаунт: {safe(account.display_name)}\n\nВыберите, какие события отправлять в topic:", reply_markup=notification_settings(account)); await call.answer()

    @router.callback_query(F.data.startswith("account:proxy:"))
    async def account_proxy_settings(call, state):
        account = await repo.get_account(int(call.data.rsplit(":", 1)[1]))
        if not account or account.telegram_user_id != call.from_user.id:
            await call.answer("Аккаунт не найден", show_alert=True)
            return
        await state.update_data(account_id=account.id)
        await state.set_state(AccountProxy.value)
        await call.message.edit_text(
            f"🌐 <b>Прокси аккаунта</b>\n\n"
            f"Аккаунт: <b>{safe(account.display_name)}</b>\n"
            f"Текущий статус: {'✅ установлен' if account.encrypted_proxy_url else '❌ не установлен'}\n\n"
            "Отправьте proxy URL в формате:\n"
            "<code>http://user:pass@host:port</code>\n"
            "<code>socks5://user:pass@host:port</code>\n\n"
            "Чтобы удалить прокси, отправьте <code>-</code>.",
            reply_markup=back(f"account:proxy_cancel:{account.id}", "🔙 Отмена"),
        )
        await call.answer()

    @router.callback_query(F.data.startswith("account:proxy_cancel:"))
    async def account_proxy_cancel(call, state):
        await state.clear()
        account = await repo.get_account(int(call.data.rsplit(":", 1)[1]))
        if not account or account.telegram_user_id != call.from_user.id:
            await call.answer("Аккаунт не найден", show_alert=True)
            return
        await call.message.edit_text(f"👤 <b>{safe(account.display_name)}</b>", reply_markup=account_actions(account.id, bool(account.topic_thread_id), account.scheduler_status))
        await call.answer()

    @router.message(AccountProxy.value)
    async def account_proxy_save(message, state):
        data = await state.get_data()
        account = await repo.get_account(data.get("account_id"))
        if not account or account.telegram_user_id != message.from_user.id:
            await state.clear()
            await message.answer("Аккаунт не найден", reply_markup=main_menu())
            return
        value = (message.text or "").strip()
        if value in {"-", "none"}:
            encrypted = None
            status = "отключён"
        elif "://" not in value:
            await message.answer("Укажите proxy URL с протоколом: http://, https:// или socks5://")
            return
        else:
            encrypted = secret_box.encrypt(value)
            status = "установлен"
        await repo.update_account(account.id, encrypted_proxy_url=encrypted)
        await state.clear()
        await message.answer(
            f"✅ Прокси для аккаунта <b>{safe(account.display_name)}</b> {status}.\n"
            "Изменение будет использовано при следующем запросе.",
            reply_markup=account_actions(account.id, bool(account.topic_thread_id), account.scheduler_status),
        )
    @router.callback_query(F.data.startswith("notify:toggle:"))
    async def notification_toggle(call):
        _, _, account_id, field = call.data.split(":", 3); account = await repo.toggle_account_notification(int(account_id), field)
        if not account or account.telegram_user_id != call.from_user.id: await call.answer("Аккаунт не найден", show_alert=True); return
        await call.message.edit_reply_markup(reply_markup=notification_settings(account)); await call.answer("Настройка обновлена")

    @router.callback_query(F.data.startswith("notify:master:"))
    async def notification_master(call):
        account = await repo.toggle_account_notifications(int(call.data.rsplit(":", 1)[1]))
        if not account or account.telegram_user_id != call.from_user.id:
            await call.answer("Аккаунт не найден", show_alert=True)
            return
        await call.message.edit_reply_markup(reply_markup=notification_settings(account))
        await call.answer("Все уведомления обновлены")
    @router.callback_query(F.data.startswith("account:topic:"))
    async def account_topic(call):
        account = await repo.get_account(int(call.data.rsplit(":", 1)[1]))
        if not account or account.telegram_user_id != call.from_user.id: await call.answer("Аккаунт не найден", show_alert=True); return
        try:
            chat = await bot.get_chat(notification_chat_id)
            if chat.type != "supergroup": raise RuntimeError(f"chat имеет тип {chat.type}, нужна супергруппа")
            if chat.is_forum is False: raise RuntimeError("в супергруппе выключены Topics")
            topic = await bot.create_forum_topic(chat_id=notification_chat_id, name=account.display_name)
            await repo.update_account(account.id, topic_chat_id=notification_chat_id, topic_thread_id=topic.message_thread_id)
            await bot.send_message(notification_chat_id, f"✅ Topic аккаунта {safe(account.display_name)} создан", message_thread_id=topic.message_thread_id)
            await call.message.edit_text("✅ Topic создан и привязан к аккаунту.", reply_markup=account_actions(account.id, True, account.scheduler_status)); await call.answer()
        except Exception as exc:
            log.exception("failed to create Telegram topic", extra={"account_id": account.id, "chat_id": notification_chat_id}); await call.answer(f"Не удалось создать topic: {exc}", show_alert=True)
    @router.callback_query(F.data.startswith("account:delete:"))
    async def account_delete(call):
        account = await repo.get_account(int(call.data.rsplit(":", 1)[1]))
        if not account or account.telegram_user_id != call.from_user.id: await call.answer("Аккаунт не найден", show_alert=True); return
        await call.message.edit_text(f"⚠️ Удалить аккаунт «{safe(account.display_name)}»?\n\nБудут остановлены его задачи и удалены настройки аккаунта.", reply_markup=delete_confirmation(account.id)); await call.answer()
    @router.callback_query(F.data.startswith("account:delete_yes:"))
    async def account_delete_yes(call):
        account_id = int(call.data.rsplit(":", 1)[1]); account = await repo.get_account(account_id)
        if not account or account.telegram_user_id != call.from_user.id: await call.answer("Аккаунт не найден", show_alert=True); return
        await manager.stop_account(account_id)
        topic_deleted = False
        if bot and account.topic_chat_id and account.topic_thread_id:
            try:
                await bot.delete_forum_topic(chat_id=account.topic_chat_id, message_thread_id=account.topic_thread_id)
                topic_deleted = True
            except Exception as exc:
                log.warning("topic could not be deleted", extra={"account_id": account_id, "error": str(exc)})
        await repo.delete_account(account_id)
        suffix = " Topic удалён." if topic_deleted else " Topic не найден или уже удалён."
        await call.message.edit_text(f"✅ Аккаунт «{safe(account.display_name)}» удалён.{suffix}", reply_markup=main_menu()); await call.answer()
    @router.callback_query(F.data.startswith("account:edit:"))
    async def account_edit(call, state):
        account_id = int(call.data.rsplit(":", 1)[1]); account = await repo.get_account(account_id)
        if not account or account.telegram_user_id != call.from_user.id: await call.answer("Аккаунт не найден", show_alert=True); return
        await state.update_data(account_id=account_id); await state.set_state(EditAccount.name); await call.message.edit_text("✏️ Редактирование аккаунта\n\nВведите новое название или отправьте `-`, чтобы оставить текущее:", reply_markup=back_menu()); await call.answer()
    @router.message(EditAccount.name)
    async def edit_name(message, state): await state.update_data(name=message.text); await state.set_state(EditAccount.domain); await message.answer("Новое Account / Domain или `-`:")
    @router.message(EditAccount.domain)
    async def edit_domain(message, state): await state.update_data(domain=message.text); await state.set_state(EditAccount.username); await message.answer("Новый username или `-`:")
    @router.message(EditAccount.username)
    async def edit_username(message, state): await state.update_data(username=message.text); await state.set_state(EditAccount.password); await message.answer("Новый пароль или `-`, чтобы оставить текущий:")
    @router.message(EditAccount.password)
    async def edit_password(message, state): await state.update_data(password=message.text); await state.set_state(EditAccount.project); await message.answer("Новый project ID/name или `-`:")
    @router.message(EditAccount.project)
    async def edit_project(message, state): await state.update_data(project=message.text); await state.set_state(EditAccount.region); await message.answer("Новый регион или `-`:")
    @router.message(EditAccount.region)
    async def edit_region(message, state): await state.update_data(region=message.text); await state.set_state(EditAccount.proxy); await message.answer("Новый HTTP proxy, `-` — оставить текущий, `none` — отключить proxy:")
    @router.message(EditAccount.proxy)
    async def edit_proxy(message, state): await state.update_data(proxy=message.text); await state.set_state(EditAccount.min_interval); await message.answer("Новый минимум задержки или `-`:")
    @router.message(EditAccount.min_interval)
    async def edit_min(message, state):
        await state.update_data(min_interval=message.text); await state.set_state(EditAccount.max_interval); await message.answer("Новый максимум задержки или `-`:")
    @router.message(EditAccount.max_interval)
    async def edit_max(message, state):
        await state.update_data(max_interval=message.text)
        data = await state.get_data(); account = await repo.get_account(data["account_id"]); values = {}
        fields = {"display_name": data["name"], "domain": data["domain"], "username": data["username"], "project_id": data["project"], "project_name": data["project"], "region": data["region"]}
        for field, value in fields.items():
            if value != "-": values[field] = value
        if data["password"] != "-": values["encrypted_password"] = secret_box.encrypt(data["password"])
        if data["proxy"] == "none": values["encrypted_proxy_url"] = None
        elif data["proxy"] != "-": values["encrypted_proxy_url"] = secret_box.encrypt(data["proxy"])
        try:
            if data["min_interval"] != "-": values["min_interval"] = max(30, int(data["min_interval"]))
            if data["max_interval"] != "-": values["max_interval"] = max(values.get("min_interval", max(30, account.min_interval)), int(data["max_interval"]))
        except ValueError: await message.answer("Интервалы должны быть целыми числами. Начните редактирование заново."); await state.clear(); return
        if "region" in values: values["network_api_url"] = f"https://{values['region']}.cloud.api.selcloud.ru/network/v2.0"
        await repo.update_account(account.id, **values); await state.clear(); await message.answer("✅ Данные аккаунта обновлены.", reply_markup=main_menu())

    @router.callback_query(F.data == "hunt:start")
    @router.callback_query(F.data.regexp(r"^hunt:page:\d+$"))
    async def hunt_start(call):
        page = int(call.data.rsplit(":", 1)[1]) if call.data.startswith("hunt:page:") else 0
        items = await repo.accounts(call.from_user.id)
        if not items: await call.answer("Сначала добавьте аккаунт", show_alert=True); return
        await call.message.edit_text("🎯 Запуск поиска\n\nСначала выберите аккаунт:", reply_markup=hunt_accounts(items, page)); await call.answer()
    @router.callback_query(F.data.startswith("hunt:account:"))
    async def hunt_account(call):
        chosen_account[call.from_user.id] = int(call.data.rsplit(":", 1)[1]); chosen_subnet[call.from_user.id] = set(); await call.message.edit_text("🎯 Запуск поиска\n\nВыберите одну подсеть для этого аккаунта:", reply_markup=subnets(TARGET_SUBNETS, set())); await call.answer()
    @router.callback_query(F.data == "hunt:back")
    async def hunt_back(call):
        account = await repo.get_account(chosen_account.get(call.from_user.id, 0))
        if account: await call.message.edit_text(f"👤 {safe(account.display_name)}", reply_markup=account_actions(account.id, bool(account.topic_thread_id), account.scheduler_status))
        else: await call.message.edit_text("Выберите аккаунт:", reply_markup=hunt_accounts(await repo.accounts(call.from_user.id)))
        await call.answer()
    @router.callback_query(F.data.startswith("subnet:toggle:"))
    async def subnet_toggle(call):
        subnet_id = call.data.rsplit(":", 1)[1]; chosen_subnet[call.from_user.id] = {subnet_id}; await call.message.edit_reply_markup(reply_markup=subnets(TARGET_SUBNETS, {subnet_id})); await call.answer("Подсеть выбрана")
    @router.callback_query(F.data == "subnet:all")
    async def subnet_all(call): await call.answer("Для одного аккаунта доступна только одна подсеть", show_alert=True)
    @router.callback_query(F.data == "subnet:none")
    async def subnet_none(call): chosen_subnet[call.from_user.id] = set(); await call.message.edit_reply_markup(reply_markup=subnets(TARGET_SUBNETS, set())); await call.answer()
    @router.callback_query(F.data == "subnet:save")
    async def subnet_save(call):
        account = await repo.get_account(chosen_account.get(call.from_user.id, 0)); ids = chosen_subnet.get(call.from_user.id, set())
        if not account or not ids: await call.answer("Сначала выберите подсеть", show_alert=True); return
        await repo.set_subnets(account.id, [BY_ID[next(iter(ids))]]); subnet = (await repo.enabled_subnets(account.id))[0]; await manager.start_pair(call.from_user.id, account, subnet, "966826e6-d301-4bb5-aa13-77a324d15f0d"); await call.message.edit_text(f"🚀 Поиск запущен\n\nАккаунт: {safe(account.display_name)}\nПодсеть: {subnet.cidr}\nРежим: burst", reply_markup=main_menu()); await call.answer()
    @router.callback_query(F.data == "task:list")
    async def task_list(call):
        tasks = [t for t in await manager.get_running_tasks() if t.telegram_user_id == call.from_user.id]
        text = "📋 <b>Активные задачи</b>\n\n"
        text += "Выберите задачу, чтобы посмотреть детали или управлять ею:" if tasks else "Сейчас нет активных задач.\n\nЗапустите поиск через кнопку «🎯 Запустить поиск»."
        await call.message.edit_text(text, reply_markup=task_list_keyboard(tasks)); await call.answer()
    @router.callback_query(F.data.startswith("scheduler:resume:"))
    async def scheduler_resume(call):
        account = await repo.get_account(int(call.data.rsplit(":", 1)[1]))
        if not account or account.telegram_user_id != call.from_user.id:
            await call.answer("Аккаунт не найден", show_alert=True)
            return
        await manager.resume_account(account.id)
        await call.answer("Планировщик возобновлён")
        await call.message.edit_text(f"▶️ <b>{safe(account.display_name)}</b> снова запущен.", reply_markup=main_menu())
    @router.callback_query(F.data.startswith("task:view:"))
    async def task_view(call):
        task = await manager.get_task(int(call.data.rsplit(":", 1)[1]))
        if not task or task.telegram_user_id != call.from_user.id: await call.answer("Задача не найдена", show_alert=True); return
        await call.message.edit_text(f"📋 <b>Задача #{task.id}</b>\n\n<b>Подсеть:</b> <code>{task.subnet_cidr}</code>\n<b>Статус:</b> {task.status}\n<b>Попыток:</b> {task.attempts}\n<b>Режим:</b> burst\n<b>Последняя ошибка:</b> {task.last_error or 'нет'}", reply_markup=task_actions(task.id)); await call.answer()
    @router.callback_query(F.data.startswith("task:pause:"))
    async def task_pause(call):
        task_id = int(call.data.rsplit(":", 1)[1]); task = await manager.get_task(task_id)
        if not task or task.telegram_user_id != call.from_user.id: await call.answer("Задача не найдена", show_alert=True); return
        await manager.pause_task(task_id); await call.message.edit_text(f"⏸ Задача #{task_id} поставлена на паузу.", reply_markup=main_menu()); await call.answer()
    @router.callback_query(F.data.startswith("task:stop:"))
    async def task_stop(call):
        task_id = int(call.data.rsplit(":", 1)[1]); task = await manager.get_task(task_id)
        if not task or task.telegram_user_id != call.from_user.id: await call.answer("Задача не найдена", show_alert=True); return
        await manager.stop_task(task_id); await call.message.edit_text(f"⏹ Задача #{task_id} остановлена.", reply_markup=main_menu()); await call.answer()
    @router.callback_query(F.data == "found:list")
    @router.callback_query(F.data.regexp(r"^found:page:\d+$"))
    async def found_list(call):
        found = await repo.found(call.from_user.id)
        page = int(call.data.rsplit(":", 1)[1]) if call.data.startswith("found:page:") else 0
        visible, page, pages = page_slice(found, page)
        lines = [f"✅ <b>Найденные IP · {len(found)}</b>", ""]
        for item in visible:
            lines += [f"🌐 <code>{safe(item.floating_ip_address)}</code>", f"Подсеть: <code>{safe(item.subnet_cidr)}</code> · {safe(item.region)}", f"Найден: {local_time(item.found_at)} · ЕКБ (UTC+5)", ""]
        if not found:
            lines.append("Адресов пока нет. Результаты поиска появятся здесь.")
        await edit_screen(call.message, "\n".join(lines), found_pages(page, pages))
        await call.answer()
    return router
