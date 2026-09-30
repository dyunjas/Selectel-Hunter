from aiogram import F, Router
from aiogram.types import CallbackQuery
from app.config.regions import REGIONS
from .keyboards import account_burst_settings, back, main_menu, scheduler_settings


def build_scheduler_ui(repo, manager):
    router = Router()

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

    @router.callback_query(F.data.startswith("account:settings:"))
    async def account_settings(call):
        account = await repo.get_account(int(call.data.rsplit(":", 1)[1]))
        if not account or account.telegram_user_id != call.from_user.id:
            await call.answer("Аккаунт не найден", show_alert=True)
            return
        settings = await repo.scheduler_settings()
        await call.message.edit_text(
            f"⚙️ <b>Настройки аккаунта</b>\n\n👤 {account.display_name}\n"
            "Параметры burst общие для scheduler и меняются кнопками ниже.\n\n"
            f"🌐 Прокси: <b>{'установлен' if account.encrypted_proxy_url else 'не установлен'}</b>\n"
            f"⚡ Между запросами: <b>{settings.burst_request_delay:g} сек</b>\n"
            f"🔄 Cooldown: <b>{settings.burst_cooldown} сек</b>\n"
            f"🌐 API timeout: <b>{settings.api_timeout:g} сек</b>\n"
            f"⚠️ Лимит ошибок: <b>{settings.errors_before_disable}</b>",
            reply_markup=account_burst_settings(settings, account.id),
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
        lines = ["📊 <b>Scheduler</b>", f"Активных аккаунтов: <b>{len(accounts)}</b>", f"Cooldown: <b>{settings.burst_cooldown} сек</b>", f"Auto stagger: <b>{stagger:g} сек</b>", "", "<b>Расписание:</b>"]
        lines += [f"{i * stagger:05.1f} сек · {account.display_name} · {account.scheduler_status}" for i, account in enumerate(accounts)]
        await call.message.edit_text("\n".join(lines), reply_markup=back())
        await call.answer()

    @router.callback_query(F.data == "stats:view")
    async def stats(call):
        values = await repo.user_attempt_stats(call.from_user.id)
        total = sum(values.values())
        found = values.get("FOUND", 0)
        no_free = values.get("NO_FREE_IP", 0)
        network = values.get("NETWORK_ERROR", 0)
        permission = values.get("PERMISSION_ERROR", 0) + values.get("AUTH_ERROR", 0)
        rate_limit = values.get("RATE_LIMIT", 0)
        await call.message.edit_text(
            "📈 <b>Статистика</b>\n\n"
            f"Запросов: <b>{total}</b>\n"
            f"✅ FOUND: <b>{found}</b>\n"
            f"ℹ️ NO_FREE_IP: <b>{no_free}</b>\n"
            f"🌐 Сетевые ошибки: <b>{network}</b>\n"
            f"⛔ 403 / авторизация: <b>{permission}</b>\n"
            f"⏱ 429: <b>{rate_limit}</b>",
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

    @router.callback_query(F.data.startswith("target:toggle:"))
    async def target_toggle(call):
        subnet_id = call.data.rsplit(":", 1)[1]
        current = {target.subnet_id for target in await repo.enabled_targets()}
        await repo.set_target_enabled(subnet_id, subnet_id not in current)
        await targets(call)

    return router
