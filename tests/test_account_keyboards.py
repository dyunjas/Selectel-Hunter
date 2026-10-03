from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from app.bot.keyboards import accounts, hunt_accounts
from app.bot.router import build_router


def items(count):
    return [SimpleNamespace(id=i, display_name=f"Аккаунт {i}", scheduler_status="RUNNING") for i in range(count)]


def callbacks(keyboard):
    return [button.callback_data for row in keyboard.inline_keyboard for button in row]


@pytest.mark.parametrize("builder,prefix", [(accounts, "account:view"), (hunt_accounts, "hunt:account")])
def test_all_accounts_reachable_with_bounded_height(builder, prefix):
    values = items(101)
    reached = []
    for page in range(9):
        keyboard = builder(values, page)
        assert len(keyboard.inline_keyboard) <= 8
        reached += [int(value.rsplit(":", 1)[1]) for value in callbacks(keyboard) if value.startswith(prefix + ":")]
    assert reached == list(range(101))


@pytest.mark.parametrize("builder", [accounts, hunt_accounts])
def test_small_lists_and_long_names(builder):
    values = items(3)
    values[0].display_name = "очень длинное имя\n" * 30
    keyboard = builder(values)
    assert all(len(row) == 1 for row in keyboard.inline_keyboard[:3])
    assert "…" in keyboard.inline_keyboard[0][0].text
    assert "\n" not in keyboard.inline_keyboard[0][0].text
    assert all(":page:" not in value for value in callbacks(keyboard))
    assert len(builder(items(8)).inline_keyboard[0]) == 2


@pytest.mark.parametrize("builder,prefix", [(accounts, "account:page"), (hunt_accounts, "hunt:page")])
def test_page_boundaries_and_deleted_accounts(builder, prefix):
    assert f"{prefix}:1" in callbacks(builder(items(25), 0))
    assert f"{prefix}:-1" not in callbacks(builder(items(25), -10))
    keyboard = builder(items(25), 999)
    assert f"{prefix}:1" in callbacks(keyboard)
    assert f"{prefix}:3" not in callbacks(keyboard)
    assert "keyboard:noop" in callbacks(keyboard)
    assert all(":page:" not in value for value in callbacks(builder(items(2), 999)))
    assert callbacks(builder([]))


@pytest.mark.asyncio
@pytest.mark.parametrize("handler_name,data,prefix", [("account_list", "account:page:1", "account:view"), ("hunt_start", "hunt:page:1", "hunt:account")])
async def test_page_handlers_load_current_users_accounts(handler_name, data, prefix):
    repo = SimpleNamespace(accounts=AsyncMock(return_value=items(25)))
    router = build_router(repo, None, None, None)
    handler = next(handler.callback for handler in router.callback_query.handlers if handler.callback.__name__ == handler_name)
    call = SimpleNamespace(data=data, from_user=SimpleNamespace(id=42), message=SimpleNamespace(edit_text=AsyncMock()), answer=AsyncMock())
    await handler(call)
    repo.accounts.assert_awaited_once_with(42)
    keyboard = call.message.edit_text.call_args.kwargs["reply_markup"]
    assert f"{prefix}:12" in callbacks(keyboard)
    assert f"{prefix}:0" not in callbacks(keyboard)
    call.answer.assert_awaited_once()
