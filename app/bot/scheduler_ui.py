from aiogram import F, Router
from aiogram.types import CallbackQuery
from app.config.regions import REGIONS
from .keyboards import account_settings_keyboard, back, global_notification_settings, main_menu, region_settings, scheduler_settings


def build_scheduler_ui(repo, manager):
    router = Router()

    @router.callback_query(F.data == "hunt:all")
    async def start_all(call):
        accounts = await repo.accounts(call.from_user.id)
        if not accounts:
            await call.answer("Сначала добавьте хотя бы один аккаунт", show_alert=True)
            return
        started, failed = await manager.start_all_accounts(call.from_user.id)
        text = f"🚀 <b>Запуск завершён</b>\n\nАккаунтов запущено: <b>{started}</b>"
        if failed:
            text += f"\nНе удалось запустить: <b>{len(failed)}</b>"
        text += "\n\nВсе аккаунты работают в общем burst-планировщике."
        await call.message.edit_text(text, reply_markup=main_menu())
        await call.answer("Все аккаунты запущены")

    async def settings_screen(call):
        settings = await repo.scheduler_settings()
        await call.message.edit_text(
            "⚙️ <b>Burst настройки</b>\n\n"
            f"⚡ Между запросами: <b>{settings.burst_request_delay:g} сек</b>\n"
            f"🔄 Cooldown: <b>{settings.burst_cooldown} сек</b>\n"
            f"🌐 API timeout: <b>{settings.api_timeout:g} сек</b>\n"
            f"⚠️ Ошибок до отключения: <b>{settings.errors_before_disable}</b>\n\n"
            "Изменения применяются к следующим burst.",
            reply_markup=scheduler_settings(settings),
        )

    @router.callback_query(F.data == "scheduler:settings")
    async def settings(call):
        await settings_screen(call)
        await call.answer()

    @router.callback_query(F.data == "notifications:global")
    async def global_notifications(call):
        settings = await repo.notification_settings()
        await call.message.edit_text(
            "🔔 <b>Настройки уведомлений</b>\n\n"
            "Общие параметры доставки. Отдельные категории можно включать в карточке аккаунта.",
            reply_markup=global_notification_settings(settings),
        )
        await call.answer()

    @router.callback_query(F.data == "notify:global:toggle")
    async def global_notifications_toggle(call):
        settings = await repo.notification_settings()
        await repo.update_notification_settings(enabled=not settings.enabled)
        await global_notifications(call)

    @router.callback_query(F.data == "notify:global:aggregate")
    async def global_notifications_aggregate(call):
        from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup
        await call.message.edit_reply_markup(reply_markup=InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text="15 сек", callback_data="notify:global:aggregate:15"), InlineKeyboardButton(text="60 сек", callback_data="notify:global:aggregate:60")],
            [InlineKeyboardButton(text="5 минут", callback_data="notify:global:aggregate:300"), InlineKeyboardButton(text="Не объединять", callback_data="notify:global:aggregate:0")],
            [InlineKeyboardButton(text="🔙 Назад", callback_data="notifications:global")],
        ]))
        await call.answer()

    @router.callback_query(F.data.startswith("notify:global:aggregate:"))
    async def global_notifications_aggregate_set(call):
        value = int(call.data.rsplit(":", 1)[1])
        await repo.update_notification_settings(aggregate_seconds=value)
        await global_notifications(call)

    @router.callback_query(F.data == "notify:global:reports")
    async def global_reports(call):
        from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup
        await call.message.edit_reply_markup(reply_markup=InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text="Выключены", callback_data="notify:global:report:off")],
            [InlineKeyboardButton(text="Каждый час", callback_data="notify:global:report:hour"), InlineKeyboardButton(text="Раз в сутки", callback_data="notify:global:report:day")],
            [InlineKeyboardButton(text="🔙 Назад", callback_data="notifications:global")],
        ]))
        await call.answer()

    @router.callback_query(F.data.startswith("notify:global:report:"))
    async def global_report_set(call):
        await repo.update_notification_settings(report_period=call.data.rsplit(":", 1)[1])
        await global_notifications(call)

    @router.callback_query(F.data.startswith("account:settings:"))
    async def account_settings(call):
        account = await repo.get_account(int(call.data.rsplit(":", 1)[1]))
        if not account or account.telegram_user_id != call.from_user.id:
            await call.answer("Аккаунт не найден", show_alert=True)
            return
        await call.message.edit_text(
            f"⚙️ <b>Настройки аккаунта</b>\n\n👤 <b>{account.display_name}</b>\n"
            f"Статус: <b>{account.scheduler_status}</b>\n"
            f"Прокси: <b>{'подключён' if account.encrypted_proxy_url else 'не настроен'}</b>\n\n"
            "Здесь находятся только индивидуальные настройки аккаунта. Общие параметры burst находятся в разделе «Настройки поиска». ",
            reply_markup=account_settings_keyboard(account),
        )
        await call.answer()

    @router.callback_query(F.data == "burst:delay")
    async def delay_menu(call):
        await call.message.edit_reply_markup(reply_markup=__import__("aiogram").types.InlineKeyboardMarkup(inline_keyboard=[
            [__import__("aiogram").types.InlineKeyboardButton(text="−1", callback_data="burst:delay:-1"), __import__("aiogram").types.InlineKeyboardButton(text="−0.1", callback_data="burst:delay:-0.1")],
            [__import__("aiogram").types.InlineKeyboardButton(text="0.3", callback_data="burst:delay:0.3"), __import__("aiogram").types.InlineKeyboardButton(text="0.5", callback_data="burst:delay:0.5"), __import__("aiogram").types.InlineKeyboardButton(text="1", callback_data="burst:delay:1"), __import__("aiogram").types.InlineKeyboardButton(text="2", callback_data="burst:delay:2")],
            [__import__("aiogram").types.InlineKeyboardButton(text="+0.1", callback_data="burst:delay:+0.1"), __import__("aiogram").types.InlineKeyboardButton(text="+1", callback_data="burst:delay:+1")],
            [__import__("aiogram").types.InlineKeyboardButton(text="🔙 Назад", callback_data="scheduler:settings")],
        ]))
        await call.answer()

    @router.callback_query(F.data.startswith("burst:delay:"))
    async def set_delay(call):
        settings = await repo.scheduler_settings()
        value = call.data.rsplit(":", 1)[1]
        if value.startswith(("+", "-")):
            new_value = max(0.1, round(settings.burst_request_delay + float(value), 1))
        else:
            new_value = float(value)
        await repo.update_scheduler_settings(burst_request_delay=new_value)
        await settings_screen(call)
        await call.answer("Задержка сохранена")

    @router.callback_query(F.data == "burst:cooldown")
    async def cooldown_menu(call):
        from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup
        await call.message.edit_reply_markup(reply_markup=InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text="−60", callback_data="burst:cooldown:-60"), InlineKeyboardButton(text="−30", callback_data="burst:cooldown:-30"), InlineKeyboardButton(text="−10", callback_data="burst:cooldown:-10")],
            [InlineKeyboardButton(text="60", callback_data="burst:cooldown:=60"), InlineKeyboardButton(text="120", callback_data="burst:cooldown:=120"), InlineKeyboardButton(text="360", callback_data="burst:cooldown:=360"), InlineKeyboardButton(text="600", callback_data="burst:cooldown:=600")],
            [InlineKeyboardButton(text="+10", callback_data="burst:cooldown:+10"), InlineKeyboardButton(text="+30", callback_data="burst:cooldown:+30"), InlineKeyboardButton(text="+60", callback_data="burst:cooldown:+60")],
            [InlineKeyboardButton(text="🔙 Назад", callback_data="scheduler:settings")],
        ]))
        await call.answer()

    @router.callback_query(F.data.startswith("burst:cooldown:"))
    async def set_cooldown(call):
        settings = await repo.scheduler_settings()
        value = call.data.rsplit(":", 1)[1]
        new_value = int(value[1:]) if value.startswith("=") else max(1, settings.burst_cooldown + int(value))
        await repo.update_scheduler_settings(burst_cooldown=new_value)
        await manager.recalculate_schedule()
        await settings_screen(call)
        await call.answer("Cooldown сохранён")

    @router.callback_query(F.data == "burst:timeout")
    async def timeout_menu(call):
        from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup
        await call.message.edit_reply_markup(reply_markup=InlineKeyboardMarkup(inline_keyboard=[[InlineKeyboardButton(text=str(value), callback_data=f"burst:timeout:{value}") for value in (3, 5, 10, 15, 30)], [InlineKeyboardButton(text="🔙 Назад", callback_data="scheduler:settings")]]))
        await call.answer()

    @router.callback_query(F.data.startswith("burst:timeout:"))
    async def set_timeout(call):
        await repo.update_scheduler_settings(api_timeout=float(call.data.rsplit(":", 1)[1]))
        await settings_screen(call)
        await call.answer("Timeout сохранён")

    @router.callback_query(F.data == "burst:errors")
    async def errors_menu(call):
        from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup
        await call.message.edit_reply_markup(reply_markup=InlineKeyboardMarkup(inline_keyboard=[[InlineKeyboardButton(text=str(value), callback_data=f"burst:errors:{value}") for value in (5, 10, 20, 30, 50)], [InlineKeyboardButton(text="🔙 Назад", callback_data="scheduler:settings")]]))
        await call.answer()

    @router.callback_query(F.data.startswith("burst:errors:"))
    async def set_errors(call):
        await repo.update_scheduler_settings(errors_before_disable=int(call.data.rsplit(":", 1)[1]))
        await settings_screen(call)
        await call.answer("Лимит ошибок сохранён")

    @router.callback_query(F.data == "scheduler:view")
    async def schedule(call):
        settings = await repo.scheduler_settings()
        accounts = await manager.get_schedule(call.from_user.id)
        stagger = settings.burst_cooldown / len(accounts) if accounts else 0
        active = sum(account.scheduler_status == "RUNNING" for account in accounts)
        waiting = sum(account.scheduler_status in {"RATE_LIMIT_COOLDOWN", "IDLE"} for account in accounts)
        lines = [
            "📊 <b>Планировщик аккаунтов</b>",
            f"Активных аккаунтов: <b>{active}</b>",
            f"Всего в расписании: <b>{len(accounts)}</b>",
            f"Период полного обхода: <b>{settings.burst_cooldown} сек</b>",
            f"Интервал между аккаунтами: <b>{stagger:g} сек</b>",
            f"Ожидают запуска или cooldown: <b>{waiting}</b>",
            "",
            "<b>Ближайшие слоты:</b>",
        ]
        for account in accounts:
            planned = account.next_cycle_at.strftime("%d.%m %H:%M:%S UTC") if account.next_cycle_at else "не запланирован"
            lines.append(f"👤 {account.display_name} · {account.scheduler_status} · {planned}")
        await call.message.edit_text("\n".join(lines), reply_markup=back())
        await call.answer()

    @router.callback_query(F.data == "stats:view")
    async def stats(call):
        accounts = await repo.accounts(call.from_user.id)
        values = await repo.user_attempt_stats(call.from_user.id)
        total = sum(values.values())
        found = values.get("FOUND", 0)
        no_free = values.get("NO_FREE_IP", 0)
        network = values.get("NETWORK_ERROR", 0)
        permission = values.get("PERMISSION_ERROR", 0) + values.get("AUTH_ERROR", 0)
        rate_limit = values.get("RATE_LIMIT", 0)
        server = values.get("SERVER_ERROR", 0)
        unknown = values.get("UNKNOWN", 0)
        running = sum(account.scheduler_status == "RUNNING" for account in accounts)
        paused = sum(account.scheduler_status == "PAUSED" for account in accounts)
        blocked = sum(account.scheduler_status == "BLOCKED" for account in accounts)
        await call.message.edit_text(
            "📈 <b>Статистика работы</b>\n\n"
            f"👤 Всего аккаунтов: <b>{len(accounts)}</b>\n"
            f"🟢 Активных: <b>{running}</b>\n"
            f"⏸ На паузе: <b>{paused}</b>\n"
            f"⛔ Заблокировано: <b>{blocked}</b>\n\n"
            f"🔄 Всего запросов: <b>{total}</b>\n"
            f"✅ Найдено IP: <b>{found}</b>\n"
            f"ℹ️ Нет свободных IP: <b>{no_free}</b>\n"
            f"🌐 Сетевые ошибки: <b>{network}</b>\n"
            f"🔐 Ошибки доступа: <b>{permission}</b>\n"
            f"⏱ Ограничения API: <b>{rate_limit}</b>\n"
            f"🖥 Ошибки сервера: <b>{server}</b>\n"
            f"❓ Другие ошибки: <b>{unknown}</b>",
            reply_markup=back(),
        )
        await call.answer()

    @router.callback_query(F.data.startswith("account:pause:"))
    async def account_pause(call):
        account = await repo.get_account(int(call.data.rsplit(":", 1)[1]))
        if not account or account.telegram_user_id != call.from_user.id:
            await call.answer("Аккаунт не найден", show_alert=True); return
        await manager.pause_account(account.id)
        await call.answer("Аккаунт поставлен на паузу")
        await call.message.edit_text(f"⏸ <b>{account.display_name}</b> поставлен на паузу.", reply_markup=main_menu())

    @router.callback_query(F.data.startswith("account:resume:"))
    async def account_resume(call):
        account = await repo.get_account(int(call.data.rsplit(":", 1)[1]))
        if not account or account.telegram_user_id != call.from_user.id:
            await call.answer("Аккаунт не найден", show_alert=True); return
        await manager.resume_account(account.id)
        await call.answer("Аккаунт возобновлён")
        await call.message.edit_text(f"▶️ <b>{account.display_name}</b> снова участвует в scheduler.", reply_markup=main_menu())

    @router.callback_query(F.data == "targets:view")
    async def targets(call):
        from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup
        all_targets = await repo.all_targets()
        enabled = {target.subnet_id for target in await repo.enabled_targets()}
        rows = []
        for target in all_targets:
            mark = "✅" if target.subnet_id in enabled else "▫️"
            rows.append([InlineKeyboardButton(text=f"{mark} {target.region} · {target.cidr}", callback_data=f"target:toggle:{target.subnet_id}")])
        rows.append([InlineKeyboardButton(text="🔙 Назад", callback_data="home")])
        await call.message.edit_text("🎯 <b>Глобальные targets</b>\n\nНажмите на подсеть, чтобы отключить её для всех аккаунтов.", reply_markup=InlineKeyboardMarkup(inline_keyboard=rows))
        await call.answer()

    @router.callback_query(F.data == "regions:view")
    async def regions(call):
        states = await repo.region_states()
        enabled = sum(1 for state in states if state.enabled)
        await call.message.edit_text(
            f"🌍 <b>Регионы</b>\n\nАктивно: <b>{enabled}/{len(states)}</b>\n"
            "Нажмите на регион, чтобы включить или выключить его для всех аккаунтов.",
            reply_markup=region_settings(states),
        )
        await call.answer()

    @router.callback_query(F.data.startswith("region:toggle:"))
    async def region_toggle(call):
        region = call.data.rsplit(":", 1)[1]
        states = await repo.region_states()
        current = next((state for state in states if state.region == region), None)
        if current is None:
            await call.answer("Регион не найден", show_alert=True)
            return
        await repo.set_region_enabled(region, not current.enabled)
        await regions(call)

    @router.callback_query(F.data.startswith("target:toggle:"))
    async def target_toggle(call):
        subnet_id = call.data.rsplit(":", 1)[1]
        current = {target.subnet_id for target in await repo.enabled_targets()}
        await repo.set_target_enabled(subnet_id, subnet_id not in current)
        await targets(call)

    return router
