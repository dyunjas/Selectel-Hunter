import logging
from aiogram import BaseMiddleware, F, Router
from aiogram.filters import Command, CommandStart
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.types import CallbackQuery, Message
from app.config.subnets import BY_ID, TARGET_SUBNETS
from app.config.regions import REGIONS
from .keyboards import account_actions, accounts, back, delete_confirmation, hunt_accounts, main_menu, notification_settings, region_picker, subnets, task_actions, task_list as task_list_keyboard

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


def build_router(repo, manager, secret_box, client_factory, admin_ids=None, bot=None, notification_chat_id=0):
    router = Router(); guard = AdminOnlyMiddleware(admin_ids or set()); router.message.outer_middleware(guard); router.callback_query.outer_middleware(guard)
    chosen_account = {}; chosen_subnet = {}

    @router.message(CommandStart())
    async def start(message): await message.answer("<b>🚀 SELECTEL IP HUNTER</b>\n\nАвтоматический поиск свободных Floating IP.\n\n<i>Выберите действие ниже:</i>", reply_markup=main_menu())
    @router.message(Command("help"))
    async def help_command(message): await message.answer("ℹ️ <b>Как пользоваться</b>\n\n1. Добавьте Selectel-аккаунт.\n2. Выберите аккаунт и одну целевую подсеть.\n3. Запустите поиск.\n4. Управляйте задачей в разделе «📋 Задачи».\n\nОшибки сети и отсутствие свободных IP не останавливают поиск — бот повторит попытку автоматически.", reply_markup=back())
    @router.message(Command("cancel"))
    async def cancel(message, state): await state.clear(); await message.answer("↩️ Текущее действие отменено.", reply_markup=main_menu())
    @router.message(Command("status"))
    async def status(message):
        tasks = await repo.user_tasks(message.from_user.id)
        await message.answer(f"<b>📊 СТАТУС СИСТЕМЫ</b>\n\n👤 Аккаунтов: <b>{len(await repo.accounts(message.from_user.id))}</b>\n🔄 Активных задач: <b>{sum(t.status == 'RUNNING' for t in tasks)}</b>\n⏸ На паузе: <b>{sum(t.status == 'PAUSED' for t in tasks)}</b>\n✅ Найдено IP: <b>{sum(t.status == 'FOUND' for t in tasks)}</b>\n⚠️ Ошибок: <b>{sum(t.status == 'ERROR' for t in tasks)}</b>", reply_markup=back_menu())
    @router.callback_query(F.data == "home")
    async def home(call, state: FSMContext): await state.clear(); await call.message.edit_text("<b>🚀 SELECTEL IP HUNTER</b>\n\nГлавное меню. Выберите раздел:", reply_markup=main_menu()); await call.answer()
    @router.callback_query(F.data == "help")
    async def help_screen(call): await call.message.edit_text("<b>ℹ️ КАК ЭТО РАБОТАЕТ</b>\n\n<b>1. Аккаунт</b>\nДобавьте Selectel credentials и настройки подключения.\n\n<b>2. Поиск</b>\nВыберите аккаунт и одну целевую подсеть.\n\n<b>3. Watcher</b>\nБот повторяет запросы с вашим интервалом и не останавливается из-за временных ошибок.\n\n<b>4. Результат</b>\nНайденный IP отправляется в topic аккаунта.\n\nКоманды: /start · /status · /help · /cancel", reply_markup=back()); await call.answer()

    @router.callback_query(F.data == "account:add")
    async def account_add_region_prompt(call, state):
        await state.set_state(AddAccount.name)
        await call.message.edit_text("➕ Добавление аккаунта\n\nШаг 1 из 9\nВведите понятное название:", reply_markup=back_menu())
        await call.answer()

    @router.callback_query(F.data == "account:add:region")
    async def account_add(call, state): await state.set_state(AddAccount.name); await call.message.edit_text("➕ Добавление аккаунта\n\nШаг 1 из 9\nВведите понятное название:", reply_markup=back_menu()); await call.answer()
    @router.message(AddAccount.name)
    async def account_name(message, state): await state.update_data(name=message.text); await state.set_state(AddAccount.domain); await message.answer("Шаг 2 из 9\nВведите Account / Domain:")
    @router.message(AddAccount.domain)
    async def account_domain(message, state): await state.update_data(domain=message.text); await state.set_state(AddAccount.username); await message.answer("Шаг 3 из 9\nВведите username:")
    @router.message(AddAccount.username)
    async def account_username(message, state): await state.update_data(username=message.text); await state.set_state(AddAccount.password); await message.answer("Шаг 4 из 9\nВведите пароль Selectel:")
    @router.message(AddAccount.password)
    async def account_password(message, state): await state.update_data(password=message.text); await state.set_state(AddAccount.project); await message.answer("Шаг 5 из 9\nВведите project ID или project name:")
    @router.message(AddAccount.project)
    async def account_project_global(message, state):
        await state.update_data(project=message.text, region="ru-3")
        await state.set_state(AddAccount.proxy)
        await message.answer("Шаг 6 из 8\nРегион аккаунта больше не задаётся. Будут проверяться все включённые регионы.\n\nВведите HTTP/SOCKS5 proxy или отправьте `-`:")
    @router.message(AddAccount.project)
    async def account_project(message, state): await state.update_data(project=message.text); await state.set_state(AddAccount.region); await message.answer("Шаг 6 из 9\nВведите регион или отправьте ru-3:")
    @router.message(AddAccount.region)
    async def account_region(message, state): await state.update_data(region=message.text or "ru-3"); await state.set_state(AddAccount.proxy); await message.answer("Шаг 7 из 9\nВведите HTTP proxy или отправьте `-`, если proxy не нужен:")
    @router.message(AddAccount.proxy)
    async def account_proxy(message, state): await state.update_data(proxy=None if message.text.strip() == "-" else message.text.strip()); await state.set_state(AddAccount.min_interval); await message.answer("Шаг 8 из 9\nМинимальная задержка между запросами, секунд (минимум 60):")
    @router.message(AddAccount.min_interval)
    async def account_min(message, state):
        try: value = max(3, int(message.text))
        except ValueError: await message.answer("Введите целое число, например 5:"); return
        await state.update_data(min_interval=max(30, value)); await state.set_state(AddAccount.max_interval); await message.answer("Шаг 9 из 9\nМаксимальная задержка, секунд:")
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
                if account.notify_account_added:
                    await bot.send_message(notification_chat_id, f"✅ Аккаунт добавлен\n{account.display_name}\nЗадержка: {account.min_interval}–{account.max_interval} сек.", message_thread_id=topic.message_thread_id)
                topic_ok = True
            except Exception as exc:
                topic_ok = False; log.exception("failed to create Telegram topic", extra={"account_id": account.id, "chat_id": notification_chat_id})
                await message.answer(f"⚠️ Аккаунт сохранён, но topic не создан.\nПричина: {type(exc).__name__}: {exc}")
        await state.clear(); await message.answer(f"✅ Аккаунт сохранён\n\nНазвание: {account.display_name}\nProxy: {'включён' if data.get('proxy') else 'не используется'}\nИнтервал: {account.min_interval}–{account.max_interval} сек.\nTopic: {'создан' if topic_ok else 'не настроен'}", reply_markup=main_menu())

    @router.callback_query(F.data == "account:list")
    async def account_list(call):
        items = await repo.accounts(call.from_user.id); text = "👤 Мои аккаунты\n\nВыберите аккаунт для просмотра настроек:" if items else "👤 Аккаунты\n\nПока нет добавленных аккаунтов."
        await call.message.edit_text(text, reply_markup=accounts(items)); await call.answer()
    @router.callback_query(F.data.startswith("account:view:"))
    async def account_view(call):
        account = await repo.get_account(int(call.data.rsplit(":", 1)[1]))
        if not account or account.telegram_user_id != call.from_user.id: await call.answer("Аккаунт не найден", show_alert=True); return
        nets = await repo.enabled_subnets(account.id); tasks = [t for t in await repo.user_tasks(call.from_user.id) if t.account_id == account.id and t.status == "RUNNING"]
        await call.message.edit_text(f"👤 {account.display_name}\n\nProxy: {'✅ включён' if account.encrypted_proxy_url else '❌ нет'}\nЗадержка: {account.min_interval}–{account.max_interval} сек.\nПодсеть: {nets[0].cidr if nets else 'не выбрана'}\nАктивных задач: {len(tasks)}", reply_markup=account_actions(account.id, bool(account.topic_thread_id))); await call.answer()
    @router.callback_query(F.data.startswith("account:hunt:"))
    async def account_hunt(call):
        account_id = int(call.data.rsplit(":", 1)[1]); account = await repo.get_account(account_id)
        if not account or account.telegram_user_id != call.from_user.id: await call.answer("Аккаунт не найден", show_alert=True); return
        chosen_account[call.from_user.id] = account_id; chosen_subnet[call.from_user.id] = set(); await call.message.edit_text(f"🎯 <b>Новый поиск</b>\n\nАккаунт: <b>{account.display_name}</b>\nВыберите одну подсеть:", reply_markup=subnets(TARGET_SUBNETS, set())); await call.answer()
    @router.callback_query(F.data.startswith("account:notifications:"))
    async def account_notifications(call):
        account = await repo.get_account(int(call.data.rsplit(":", 1)[1]))
        if not account or account.telegram_user_id != call.from_user.id: await call.answer("Аккаунт не найден", show_alert=True); return
        await call.message.edit_text(f"🔔 Уведомления\n\nАккаунт: {account.display_name}\n\nВыберите, какие события отправлять в topic:", reply_markup=notification_settings(account)); await call.answer()
    @router.callback_query(F.data.startswith("notify:toggle:"))
    async def notification_toggle(call):
        _, _, account_id, field = call.data.split(":", 3); account = await repo.toggle_account_notification(int(account_id), field)
        if not account or account.telegram_user_id != call.from_user.id: await call.answer("Аккаунт не найден", show_alert=True); return
        await call.message.edit_reply_markup(reply_markup=notification_settings(account)); await call.answer("Настройка обновлена")
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
            await bot.send_message(notification_chat_id, f"✅ Topic аккаунта {account.display_name} создан", message_thread_id=topic.message_thread_id)
            await call.message.edit_text("✅ Topic создан и привязан к аккаунту.", reply_markup=account_actions(account.id, True)); await call.answer()
        except Exception as exc:
            log.exception("failed to create Telegram topic", extra={"account_id": account.id, "chat_id": notification_chat_id}); await call.answer(f"Не удалось создать topic: {exc}", show_alert=True)
    @router.callback_query(F.data.startswith("account:delete:"))
    async def account_delete(call):
        account = await repo.get_account(int(call.data.rsplit(":", 1)[1]))
        if not account or account.telegram_user_id != call.from_user.id: await call.answer("Аккаунт не найден", show_alert=True); return
        await call.message.edit_text(f"⚠️ Удалить аккаунт «{account.display_name}»?\n\nБудут остановлены его задачи и удалены настройки аккаунта.", reply_markup=delete_confirmation(account.id)); await call.answer()
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
        await call.message.edit_text(f"✅ Аккаунт «{account.display_name}» удалён.{suffix}", reply_markup=main_menu()); await call.answer()
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
    async def hunt_start(call):
        items = await repo.accounts(call.from_user.id)
        if not items: await call.answer("Сначала добавьте аккаунт", show_alert=True); return
        await call.message.edit_text("🎯 Запуск поиска\n\nСначала выберите аккаунт:", reply_markup=hunt_accounts(items)); await call.answer()
    @router.callback_query(F.data.startswith("hunt:account:"))
    async def hunt_account(call):
        chosen_account[call.from_user.id] = int(call.data.rsplit(":", 1)[1]); chosen_subnet[call.from_user.id] = set(); await call.message.edit_text("🎯 Запуск поиска\n\nВыберите одну подсеть для этого аккаунта:", reply_markup=subnets(TARGET_SUBNETS, set())); await call.answer()
    @router.callback_query(F.data == "hunt:back")
    async def hunt_back(call):
        account = await repo.get_account(chosen_account.get(call.from_user.id, 0))
        if account: await call.message.edit_text(f"👤 {account.display_name}", reply_markup=account_actions(account.id, bool(account.topic_thread_id)))
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
        await repo.set_subnets(account.id, [BY_ID[next(iter(ids))]]); subnet = (await repo.enabled_subnets(account.id))[0]; await manager.start_pair(call.from_user.id, account, subnet, "966826e6-d301-4bb5-aa13-77a324d15f0d"); await call.message.edit_text(f"🚀 Поиск запущен\n\nАккаунт: {account.display_name}\nПодсеть: {subnet.cidr}\nЗадержка: {account.min_interval}–{account.max_interval} сек.", reply_markup=main_menu()); await call.answer()
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
        await call.message.edit_text(f"▶️ <b>{account.display_name}</b> снова запущен.", reply_markup=main_menu())
    @router.callback_query(F.data.startswith("task:view:"))
    async def task_view(call):
        task = await manager.get_task(int(call.data.rsplit(":", 1)[1]))
        if not task or task.telegram_user_id != call.from_user.id: await call.answer("Задача не найдена", show_alert=True); return
        await call.message.edit_text(f"📋 <b>Задача #{task.id}</b>\n\n<b>Подсеть:</b> <code>{task.subnet_cidr}</code>\n<b>Статус:</b> {task.status}\n<b>Попыток:</b> {task.attempts}\n<b>Интервал:</b> {task.min_interval}–{task.max_interval} сек.\n<b>Последняя ошибка:</b> {task.last_error or 'нет'}", reply_markup=task_actions(task.id)); await call.answer()
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
    async def found_list(call):
        found = await repo.found(call.from_user.id); text = "✅ Найденные IP\n\n" + ("\n\n".join(f"🌐 {x.floating_ip_address}\nПодсеть: {x.subnet_cidr}" for x in found) or "Пока ничего не найдено")
        await call.message.edit_text(text, reply_markup=back_menu()); await call.answer()
    return router
