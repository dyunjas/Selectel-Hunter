from datetime import datetime
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

import pytest
from aiogram.exceptions import TelegramBadRequest
from aiogram.fsm.context import FSMContext
from aiogram.fsm.storage.base import StorageKey
from aiogram.fsm.storage.memory import MemoryStorage
from aiogram.methods import EditMessageText

from app.bot.keyboards import account_actions, schedule_controls
from app.bot.multisubnet import build_multisubnet_router
from app.bot.router import AddAccount, build_router
from app.bot.scheduler_ui import build_scheduler_ui
from app.bot.views import edit_screen, schedule_text


def account(index, name="Account", status="RUNNING"):
    return SimpleNamespace(
        id=index, display_name=name, scheduler_status=status,
        enabled=True, telegram_user_id=42, next_cycle_at=datetime(2026, 10, 11, 10),
        cooldown_until=None, topic_thread_id=None,
    )


def callback(data):
    return SimpleNamespace(data=data, from_user=SimpleNamespace(id=42),
                           message=SimpleNamespace(edit_text=AsyncMock(), edit_reply_markup=AsyncMock(), answer=AsyncMock()),
                           answer=AsyncMock())


def handler(router, name, kind="callback_query"):
    return next(item.callback for item in getattr(router, kind).handlers if item.callback.__name__ == name)


def callbacks(keyboard):
    return [button.callback_data for row in keyboard.inline_keyboard for button in row]


def test_schedule_pages_fit_telegram_and_reach_every_account():
    queue = [{"account": account(index, "<&>" * 40), "status": "WAITING"} for index in range(101)]
    snapshot = {"queue": queue, "running_account_ids": []}
    reached = []
    for page in range(13):
        text, visible, selected, pages = schedule_text(snapshot, 120, 21, page)
        assert len(text) < 4096
        assert "5.71" in text
        assert "15:00:00" in text
        assert "<&>" not in text
        reached.extend(item["account"].id for item in visible)
        keyboard = schedule_controls(visible, selected, pages)
        assert len(keyboard.inline_keyboard) <= 7
        assert all(len(value.encode()) <= 64 for value in callbacks(keyboard))
    assert reached == list(range(101))
    _, visible, page, pages = schedule_text(snapshot, 120, 21, 999)
    assert page == pages - 1
    assert [item["account"].id for item in visible] == list(range(96, 101))


@pytest.mark.asyncio
async def test_unchanged_refresh_is_accepted_but_other_telegram_errors_propagate():
    message = SimpleNamespace(edit_text=AsyncMock())
    method = EditMessageText(chat_id=1, message_id=1, text="Status")
    message.edit_text.side_effect = TelegramBadRequest(method=method, message="Bad Request: message is not modified")
    await edit_screen(message, "Status", None)
    message.edit_text.side_effect = TelegramBadRequest(method=method, message="Bad Request: message to edit not found")
    with pytest.raises(TelegramBadRequest):
        await edit_screen(message, "Status", None)


@pytest.mark.asyncio
async def test_schedule_handler_uses_global_count_and_clamps_stale_page():
    values = [account(index) for index in range(21)]
    repo = SimpleNamespace(scheduler_settings=AsyncMock(return_value=SimpleNamespace(burst_cooldown=120)), all_accounts=AsyncMock(return_value=values))
    snapshot = {"queue": [{"account": item, "status": "WAITING"} for item in values[:9]], "running_account_ids": []}
    manager = SimpleNamespace(get_scheduler_snapshot=AsyncMock(return_value=snapshot), _active=lambda value: True)
    router = build_scheduler_ui(repo, manager)
    call = callback("scheduler:page:99")
    await handler(router, "schedule")(call)
    manager.get_scheduler_snapshot.assert_awaited_once_with(42)
    text = call.message.edit_text.call_args.args[0]
    keyboard = call.message.edit_text.call_args.kwargs["reply_markup"]
    assert "5.71" in text
    assert "scheduler:page:1" in callbacks(keyboard)
    assert "account:view:8" in callbacks(keyboard)
    assert "account:view:0" not in callbacks(keyboard)
    call.answer.assert_awaited_once()


@pytest.mark.asyncio
async def test_adjusting_period_keeps_editor_open_with_new_value():
    settings = SimpleNamespace(burst_cooldown=120, burst_request_delay=0.5, api_timeout=5, errors_before_disable=20)
    repo = SimpleNamespace(scheduler_settings=AsyncMock(return_value=settings))

    async def update(**values):
        for key, value in values.items():
            setattr(settings, key, value)

    repo.update_scheduler_settings = AsyncMock(side_effect=update)
    manager = SimpleNamespace(recalculate_schedule=AsyncMock())
    call = callback("burst:cooldown:+10")
    await handler(build_scheduler_ui(repo, manager), "set_cooldown")(call)
    assert settings.burst_cooldown == 130
    assert "130" in call.message.edit_text.call_args.args[0]
    assert "burst:cooldown:+10" in callbacks(call.message.edit_text.call_args.kwargs["reply_markup"])
    manager.recalculate_schedule.assert_awaited_once()
    call.answer.assert_awaited_once()


@pytest.mark.asyncio
async def test_add_account_skip_proxy_completes_and_clears_fsm():
    state = FSMContext(storage=MemoryStorage(), key=StorageKey(bot_id=1, chat_id=42, user_id=42))
    await state.set_state(AddAccount.proxy)
    await state.update_data(name="<Name>", domain="domain", username="user", password="password", project="project", region="ru-3")
    created = account(7, "<Name>", "IDLE")
    repo = SimpleNamespace(add_account=AsyncMock(return_value=created))
    secret_box = SimpleNamespace(encrypt=Mock(return_value="encrypted"))
    router = build_router(repo, None, secret_box, None)
    call = callback("account:add:skip_proxy")
    await handler(router, "skip_account_proxy")(call, state)
    assert repo.add_account.call_args.kwargs["encrypted_proxy_url"] is None
    assert await state.get_state() is None
    assert await state.get_data() == {}
    text = call.message.answer.call_args.args[0]
    assert "&lt;Name&gt;" in text
    assert "account:hunt:7" in callbacks(call.message.answer.call_args.kwargs["reply_markup"])


@pytest.mark.asyncio
async def test_selector_returns_to_selected_account_without_legacy_state():
    state = FSMContext(storage=MemoryStorage(), key=StorageKey(bot_id=1, chat_id=42, user_id=42))
    selected_account = account(7)
    repo = SimpleNamespace(get_account=AsyncMock(return_value=selected_account), all_targets=AsyncMock(return_value=[]))
    router = build_multisubnet_router(repo, None)
    call = callback("account:hunt:7")
    await handler(router, "account_hunt")(call, state)
    keyboard = call.message.edit_text.call_args.kwargs["reply_markup"]
    assert "account:view:7" in callbacks(keyboard)
    assert "hunt:back" not in callbacks(keyboard)


@pytest.mark.parametrize("status,action", [("RUNNING", "pause"), ("RATE_LIMIT_COOLDOWN", "pause"), ("PAUSED", "resume"), ("IDLE", "resume")])
def test_account_card_offers_only_relevant_run_action(status, action):
    values = callbacks(account_actions(7, status=status))
    assert f"account:{action}:7" in values
    assert not ({"account:pause:7", "account:resume:7"} <= set(values))
