import json
import asyncio
from datetime import datetime, timedelta
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
import pytest_asyncio
from aiogram.exceptions import TelegramBadRequest, TelegramRetryAfter
from aiogram.methods import EditMessageText
from aiogram.fsm.context import FSMContext
from aiogram.fsm.storage.memory import MemoryStorage
from aiogram.fsm.storage.base import StorageKey
from sqlalchemy import text

from app.config.regions import REGIONS
from app.config.subnets import TARGET_SUBNETS
from app.db.database import Database
from app.db.repositories import Repository
from app.bot.presentation import regions_for, region_name, timestamp, duration, friendly_error, keyboard, job_text
from app.bot.screens import UiScreens
from app.bot.live import LiveCards
from app.bot.experience import build_experience_router
from app.bot.jobs import build_jobs_router
from app.bot.multisubnet import build_multisubnet_router
from app.services.notifications import NotificationService
from app.bot.polling import resilient_polling
from app.bot.views import edit_screen
from app.bot.safety import UiErrorBoundary


@pytest_asyncio.fixture
async def ctx(tmp_path):
    db = Database(f"sqlite+aiosqlite:///{(tmp_path / 'ui.db').as_posix()}")
    await db.init()
    repo = Repository(db.sessions)
    account = await repo.add_account(telegram_user_id=42, display_name='<Account & "name">', domain="d", username="u",
        encrypted_password="encrypted", project_id="project", scheduler_status="RUNNING")
    manager = SimpleNamespace(resume_account=AsyncMock(), start_account=AsyncMock(), _scheduler_task=None)
    yield db, repo, account, manager
    await db.close()


def subnet(region):
    return next(t for t in TARGET_SUBNETS if t['region'] == region)


async def enqueue(repo, account, regions=("ru-2",)):
    await repo.queue.enqueue(account, [subnet(r) for r in regions])
    return await repo.queue.list(account_id=account.id)


async def complete(repo, account):
    job = await repo.queue.claim(account.id)
    await repo.queue.begin_operation(job, REGIONS[job.region]['floating_network_id'])
    await repo.queue.complete(job, {"id": f"fip-{job.id}", "floating_ip_address": job.cidr.split('/')[0]})
    return await repo.queue.get(job.id)


def call(data, user_id=42):
    return SimpleNamespace(data=data, from_user=SimpleNamespace(id=user_id), message=SimpleNamespace(
        chat=SimpleNamespace(id=42, type='private'), message_id=10, edit_text=AsyncMock(), answer=AsyncMock()), answer=AsyncMock())


def handler(router, name, kind='callback_query'):
    return next(h.callback for h in getattr(router, kind).handlers if h.callback.__name__ == name)


def callbacks(markup):
    return [b.callback_data for row in markup.inline_keyboard for b in row]


def assert_screen(rendered):
    text, markup = rendered
    assert 0 < len(text) < 4096
    assert all(1 <= len(value.encode('utf-8')) <= 64 for value in callbacks(markup))
    assert '<Account' not in text


def test_city_groups_use_existing_config():
    assert regions_for('spb') == tuple(r for r in REGIONS if r != 'ru-2')
    assert regions_for('ru-2') == ('ru-2',)
    assert regions_for('invalid') == ()
    assert set(regions_for('spb')) == {'ru-1', 'ru-3', 'ru-9'}
    assert region_name('ru-9') == 'Санкт-Петербург (ru-9)'
    assert REGIONS['ru-2']['floating_network_id'] == '8cc4ee6a-8dc9-4865-8877-ef5b5cdef020'


@pytest.mark.asyncio
async def test_preferences_persist_without_mutating_job_regions(ctx):
    db, repo, account, _ = ctx
    jobs = await enqueue(repo, account, ('ru-2', 'ru-1', 'ru-3', 'ru-9'))
    before = [(j.id, j.region, j.subnet_id) for j in jobs]
    await repo.ui.preferences(42, selected_region='ru-2', utc_offset_minutes=180, refresh_seconds=30)
    await repo.ui.preferences(42, selected_region='spb')
    restarted = Repository(db.sessions)
    pref = await restarted.ui.preferences(42)
    assert pref.selected_region == 'spb' and pref.utc_offset_minutes == 180 and pref.refresh_seconds == 30
    assert [(j.id, j.region, j.subnet_id) for j in await restarted.queue.list()] == before
    assert (await restarted.ui.preferences(99)).selected_region == 'spb'


@pytest.mark.asyncio
async def test_new_screens_use_real_stats_escape_and_fit_telegram(ctx):
    _, repo, account, manager = ctx
    await enqueue(repo, account, ('ru-2', 'ru-3', 'ru-9'))
    job = await complete(repo, account)
    screens = UiScreens(repo, manager)
    rendered = [await screens.dashboard(42), await screens.listing(42), await screens.settings(42),
                await screens.stats(42), await screens.stats(42, 'ru-2'), await screens.stats(42, 'spb'),
                await screens.job(42, job.id), await screens.history(42)]
    item = (await repo.ui.history(42))[0][0]
    rendered.append(await screens.ip(42, item.id))
    for screen in rendered:
        assert_screen(screen)
    assert await screens.job(999, job.id) is None
    assert await screens.ip(999, item.id) is None
    stats = await repo.ui.stats(42, 'ru-2')
    assert stats['found'] == 1 and stats['attempts'] == 1 and stats['active'] == 0
    assert stats['hour'] == 1 and stats['day'] == 1 and stats['success']
    spb = await repo.ui.stats(42, 'spb')
    assert spb['jobs'] == 2 and spb['active'] == 2 and spb['found'] == 0
    assert (await repo.ui.stats(999))['found'] == 0
    assert 'Сейчас не проверялся' in (await screens.history(42))[0]
    assert (await repo.ui.observation(item.id)) is None


@pytest.mark.asyncio
async def test_empty_and_legacy_telemetry_is_not_invented(ctx):
    _, repo, account, manager = ctx
    assert (await repo.ui.stats(42))['rate'] is None
    job = (await enqueue(repo, account))[0]
    async with repo.sessions() as s:
        await s.execute(text('DELETE FROM job_ui_states'))
        await s.commit()
    screen = await UiScreens(repo, manager).job(42, job.id)
    assert 'Время поиска: <b>Нет данных</b>' in screen[0]
    assert 'Последняя попытка: Нет данных' in screen[0]


@pytest.mark.asyncio
async def test_stop_requires_confirmation_and_ownership(ctx):
    _, repo, account, manager = ctx
    job = (await enqueue(repo, account))[0]
    router = build_jobs_router(repo, manager, {42})
    action = handler(router, 'job_action')
    await action(call(f'job:stop:{job.id}'))
    assert (await repo.queue.get(job.id)).status == 'pending'
    await action(call(f'job:confirm:{job.id}', 999))
    assert (await repo.queue.get(job.id)).status == 'pending'
    await action(call(f'job:confirm:{job.id}'))
    assert (await repo.queue.get(job.id)).status == 'cancelled'
    await action(call(f'job:resume:{job.id}'))
    assert (await repo.queue.get(job.id)).status == 'pending'
    manager.resume_account.assert_awaited_once_with(account.id)


@pytest.mark.asyncio
async def test_drafts_survive_fsm_restart_and_cannot_cross_city_or_owner(ctx):
    db, repo, account, manager = ctx
    router = build_multisubnet_router(repo, manager, {42})
    state = FSMContext(storage=MemoryStorage(), key=StorageKey(bot_id=1, chat_id=42, user_id=42))
    select = call(f'huntregion:spb:{account.id}')
    await handler(router, 'select_region')(select, state)
    markup = select.message.edit_text.call_args.kwargs['reply_markup']
    assert all(len(c.encode()) <= 64 for c in callbacks(markup))
    draft_id = next(c.split(':')[1] for c in callbacks(markup) if c.startswith('s:'))
    await state.clear()
    action = handler(router, 'draft_action')
    await action(call(f's:{draft_id}:toggle:{subnet("ru-2")["subnet_id"]}'))
    assert json.loads((await repo.ui.draft(draft_id, 42)).selected_json) == []
    await action(call(f's:{draft_id}:all', 999))
    assert json.loads((await repo.ui.draft(draft_id, 42)).selected_json) == []
    await action(call(f's:{draft_id}:all'))
    restarted = Repository(db.sessions)
    assert len(json.loads((await restarted.ui.draft(draft_id, 42)).selected_json)) == len([t for t in TARGET_SUBNETS if t['region'] != 'ru-2'])
    await action(call(f's:{draft_id}:save'))
    jobs = await repo.queue.list(user_id=42)
    assert set(j.region for j in jobs) == {'ru-1', 'ru-3', 'ru-9'}
    assert not any(j.region == 'ru-2' for j in jobs)
    await action(call(f's:{draft_id}:save'))
    assert len(await repo.queue.list(user_id=42)) == len(jobs)


@pytest.mark.asyncio
async def test_hour_counts_are_operations_not_heartbeats(ctx):
    _, repo, account, _ = ctx
    await enqueue(repo, account)
    job = await repo.queue.claim(account.id)
    await repo.queue.begin_operation(job, 'network')
    for _ in range(4):
        await repo.queue.heartbeat(job)
    assert (await repo.ui.stats(42))['hour'] == 1
    assert (await repo.ui.job_state(job.id)).last_attempt_at


@pytest.mark.asyncio
async def test_outage_episode_is_deduplicated_persistently_and_recovers(ctx):
    db, repo, account, _ = ctx
    job = (await enqueue(repo, account))[0]
    for _ in range(3):
        await repo.queue.control(job.id, 42, 'pending')
        current = await repo.queue.claim(account.id)
        await repo.queue.release(current, error='SERVER_ERROR: HTTP 503', delay=0)
        rows = [i for i in await repo.queue.pending_notifications() if i.event_type == 'SERVER_ERROR']
        if rows:
            await repo.queue.notification_result(rows[0], True)
        repo = Repository(db.sessions)
    assert not [i for i in await repo.queue.pending_notifications() if i.event_type == 'SERVER_ERROR']
    current = await repo.queue.claim(account.id)
    await repo.queue.release(current, error='NO_FREE_IP: HTTP 409', clear_operation=True, delay=0)
    rows = await repo.queue.pending_notifications()
    assert len([i for i in rows if i.event_type == 'RECOVERED']) == 1
    assert not [i for i in rows if i.event_type == 'NO_FREE_IP']
    current = await repo.queue.claim(account.id)
    await repo.queue.release(current, error='SERVER_ERROR: HTTP 503', delay=0)
    assert len([i for i in await repo.queue.pending_notifications() if i.event_type == 'SERVER_ERROR']) == 1


@pytest.mark.asyncio
async def test_found_notification_has_correct_links_and_retries_without_loss(ctx):
    db, repo, account, _ = ctx
    await enqueue(repo, account)
    job = await complete(repo, account)
    item = (await repo.ui.history(42))[0][0]
    task = await repo.get_task(item.hunter_task_id)
    bot = SimpleNamespace(send_message=AsyncMock(side_effect=ConnectionError('offline')))
    service = NotificationService(bot, repo)
    with pytest.raises(ConnectionError):
        await service.send(task, item.floating_ip_address, item.floating_ip_id, 0, 'FOUND', durable=True)
    bot.send_message.side_effect = None
    assert await service.send(task, item.floating_ip_address, item.floating_ip_id, 0, 'FOUND', durable=True)
    args, kwargs = bot.send_message.call_args
    assert 'Москва (ru-2)' in args[1] and item.floating_ip_address in args[1]
    assert 'Selectel. Результат сохранён в БД' in args[1]
    assert f'job:view:{job.id}' in callbacks(kwargs['reply_markup'])
    assert f'ip:view:{item.id}' in callbacks(kwargs['reply_markup'])
    event = next(i for i in await repo.queue.pending_notifications() if i.event_type == 'FOUND')
    await repo.queue.notification_result(event, True)
    restarted = Repository(db.sessions)
    assert not [i for i in await restarted.queue.pending_notifications() if i.event_type == 'FOUND']


@pytest.mark.asyncio
async def test_stale_outbox_ack_cannot_consume_new_transition(ctx):
    _, repo, account, _ = ctx
    job = (await enqueue(repo, account))[0]
    await repo.queue.control(job.id, 42, 'paused')
    old = next(i for i in await repo.queue.pending_notifications() if i.event_type == 'PAUSED')
    await repo.queue.control(job.id, 42, 'pending')
    await repo.queue.control(job.id, 42, 'paused')
    await repo.queue.notification_result(old, True)
    assert any(i.event_type == 'PAUSED' for i in await repo.queue.pending_notifications())


@pytest.mark.asyncio
async def test_paused_duration_is_real_and_unchanged_heartbeat_does_not_advance_it(ctx, monkeypatch):
    import app.db.queue as queue_module
    _, repo, account, _ = ctx
    at = datetime(2026, 10, 25, 10)
    monkeypatch.setattr(queue_module, 'now', lambda: at)
    job = (await enqueue(repo, account))[0]
    at += timedelta(minutes=10)
    await repo.queue.control(job.id, 42, 'paused')
    state = await repo.ui.job_state(job.id)
    assert state.active_seconds == 600 and state.active_since is None and state.paused_at == at
    at += timedelta(hours=3)
    rendered = job_text(await repo.queue.get(job.id), state, account, 180, at=at)
    assert '10 мин.' in rendered and '13:10:00' in rendered


def test_time_and_error_presentations_are_honest():
    assert timestamp(datetime(2026, 10, 25, 10), 300) == '25.10.2026 15:00:00'
    assert timestamp(None) == 'Нет данных'
    assert duration(None) == 'Нет данных'
    assert duration(225300) == '2 д. 14 ч. 35 мин.'
    assert 'secret' not in friendly_error('UNKNOWN: password=secret traceback...')


def live_setup():
    markup = keyboard([('Назад', 'home')])
    repo = SimpleNamespace(ui=SimpleNamespace(preferences=AsyncMock(return_value=SimpleNamespace(refresh_seconds=20))))
    screens = SimpleNamespace(repo=repo, job=AsyncMock(return_value=('unchanged', markup)))
    bot = SimpleNamespace(edit_message_text=AsyncMock())
    live = LiveCards(bot, screens, max_cards=2)
    message = SimpleNamespace(chat=SimpleNamespace(id=42), message_id=10)
    live.watch(message, 42, 'a' * 32, ('unchanged', markup), 0)
    return live, bot, screens, message, markup


@pytest.mark.asyncio
async def test_live_card_skips_unchanged_text_and_updates_same_message():
    live, bot, screens, message, markup = live_setup()
    await live.tick()
    bot.edit_message_text.assert_not_awaited()
    screens.job.return_value = ('changed', markup)
    live.cards[(42, 10)].next_at = 0
    await live.tick()
    assert bot.edit_message_text.call_args.kwargs['message_id'] == 10
    assert len(live.cards) == 1


@pytest.mark.asyncio
@pytest.mark.parametrize('failure', ['deleted', 'flood', 'unchanged'])
async def test_live_card_handles_telegram_failures(failure):
    live, bot, screens, _, markup = live_setup()
    screens.job.return_value = ('changed', markup)
    method = EditMessageText(chat_id=42, message_id=10, text='changed')
    if failure == 'flood':
        bot.edit_message_text.side_effect = TelegramRetryAfter(method=method, message='wait', retry_after=60)
    else:
        bot.edit_message_text.side_effect = TelegramBadRequest(method=method, message='message to edit not found' if failure == 'deleted' else 'message is not modified')
    await live.tick()
    assert bool(live.cards) == (failure != 'deleted')
    if failure == 'flood':
        before = bot.edit_message_text.await_count
        await live.tick()
        assert bot.edit_message_text.await_count == before


@pytest.mark.asyncio
async def test_live_subscriptions_are_bounded_expire_and_stop_on_navigation():
    live, _, _, message, markup = live_setup()
    for user_id in range(10):
        message.message_id = user_id
        live.watch(message, user_id, 'a'*32, ('unchanged', markup), 20)
    assert len(live.cards) == 2
    await live(AsyncMock(), call('home'), {})
    for card in live.cards.values():
        card.expires_at = 0
    await live.tick()
    assert not live.cards


@pytest.mark.asyncio
async def test_ip_status_check_is_read_only_and_region_scoped(ctx):
    _, repo, account, manager = ctx
    await enqueue(repo, account)
    job = await complete(repo, account)
    item = (await repo.ui.history(42))[0][0]
    client = SimpleNamespace(get_floating_ip=AsyncMock(return_value={'id': item.floating_ip_id, 'project_id': 'project', 'status': 'DOWN'}))
    router = build_experience_router(repo, manager, {42}, AsyncMock(return_value=client))
    await handler(router, 'ip')(call(f'ip:check:{item.id}'))
    client.get_floating_ip.assert_awaited_once_with(item.floating_ip_id, 'ru-2')
    assert (await repo.ui.observation(item.id)).status == 'DOWN'
    assert (await repo.queue.get(job.id)).region == 'ru-2'
    assert (await repo.queue.get(job.id)).status == 'completed'


@pytest.mark.asyncio
async def test_no_found_claim_before_committed_result(ctx):
    _, repo, account, _ = ctx
    job = (await enqueue(repo, account))[0]
    bot = SimpleNamespace(send_message=AsyncMock())
    task = SimpleNamespace(id=1234, account_id=account.id, telegram_user_id=42,
        subnet_id=job.subnet_id, subnet_cidr=job.cidr, region=job.region, attempts=0)
    assert not await NotificationService(bot, repo).send(task, '5.178.85.1', 'unconfirmed', 0, 'FOUND', durable=True)
    bot.send_message.assert_not_awaited()


@pytest.mark.asyncio
async def test_error_cooldown_groups_subnets_and_survives_service_restart(ctx):
    _, repo, account, _ = ctx
    job = (await enqueue(repo, account))[0]
    bot = SimpleNamespace(send_message=AsyncMock())
    subject = SimpleNamespace(account_id=account.id, telegram_user_id=42, region='ru-2', subnet_id=job.subnet_id, subnet_cidr=job.cidr, attempts=1)
    assert await NotificationService(bot, repo).send(subject, None, None, 0, 'SERVER_ERROR: 503', durable=True)
    subject.subnet_id = 'another-subnet'
    assert await NotificationService(bot, repo).send(subject, None, None, 0, 'SERVER_ERROR: 503', durable=True)
    bot.send_message.assert_awaited_once()
    subject.region = 'ru-9'
    assert await NotificationService(bot, repo).send(subject, None, None, 0, 'SERVER_ERROR: 503', durable=True)
    assert bot.send_message.await_count == 2


@pytest.mark.asyncio
async def test_failed_error_delivery_is_not_consumed_by_cooldown(ctx):
    _, repo, account, _ = ctx
    job = (await enqueue(repo, account))[0]
    bot = SimpleNamespace(send_message=AsyncMock(side_effect=ConnectionError()))
    subject = SimpleNamespace(account_id=account.id, telegram_user_id=42, region='ru-2', subnet_id=job.subnet_id, subnet_cidr=job.cidr, attempts=1)
    service = NotificationService(bot, repo)
    with pytest.raises(ConnectionError):
        await service.send(subject, None, None, 0, 'NETWORK_ERROR', durable=True)
    bot.send_message.side_effect = None
    assert await service.send(subject, None, None, 0, 'NETWORK_ERROR', durable=True)
    assert bot.send_message.await_count == 2


@pytest.mark.asyncio
async def test_multiple_found_notifications_stay_independent_and_persist(ctx):
    db, repo, account, _ = ctx
    await enqueue(repo, account, ('ru-2', 'ru-1', 'ru-3', 'ru-9'))
    for _ in range(4):
        await complete(repo, account)
    events = [i for i in await repo.queue.pending_notifications() if i.event_type == 'FOUND']
    assert len(events) == 4 and len({i.job_id for i in events}) == 4
    for item in events:
        await repo.queue.notification_result(item, True)
    assert not [i for i in await Repository(db.sessions).queue.pending_notifications() if i.event_type == 'FOUND']
    first_page, _, pages, total = await repo.ui.history(42, size=2)
    second_page, _, _, _ = await repo.ui.history(42, size=2, page=1)
    assert total == 4 and pages == 2
    assert {i.id for i in first_page}.isdisjoint({i.id for i in second_page})


@pytest.mark.asyncio
async def test_draft_toggles_are_atomic_and_storage_bounded(ctx):
    _, repo, account, _ = ctx
    draft = await repo.ui.create_draft(42, account.id, 'ru-2', [])
    await asyncio.gather(*(repo.ui.draft(draft.id, 42, toggle=str(i)) for i in range(6)))
    assert set(json.loads((await repo.ui.draft(draft.id, 42)).selected_json)) == set(map(str, range(6)))
    for _ in range(22):
        await repo.ui.create_draft(42, account.id, 'spb', [])
    assert await repo.ui.draft(draft.id, 42) is None
    async with repo.sessions() as s:
        assert await s.scalar(text('SELECT COUNT(*) FROM subnet_drafts')) == 20


@pytest.mark.asyncio
async def test_navigation_waits_for_inflight_live_edit():
    live, bot, screens, _, markup = live_setup()
    screens.job.return_value = ('changed', markup)
    entered, release = asyncio.Event(), asyncio.Event()
    order = []
    async def edit(**kwargs):
        entered.set()
        await release.wait()
        order.append('live')
    bot.edit_message_text.side_effect = edit
    tick = asyncio.create_task(live.tick())
    await entered.wait()
    async def navigation(*args):
        order.append('navigation')
    navigate = asyncio.create_task(live(navigation, call('home'), {}))
    await asyncio.sleep(0)
    assert order == []
    release.set()
    await asyncio.gather(tick, navigate)
    assert order == ['live', 'navigation'] and not live.cards


@pytest.mark.asyncio
async def test_manual_refresh_does_not_edit_identical_content():
    markup = keyboard([('Назад', 'home')])
    message = SimpleNamespace(html_text='<b>Статус</b>', reply_markup=markup, edit_text=AsyncMock())
    await edit_screen(message, '<b>Статус</b>', markup)
    message.edit_text.assert_not_awaited()


@pytest.mark.asyncio
async def test_stale_callback_error_is_explained():
    callback = call('old:broken')
    await UiErrorBoundary()(AsyncMock(side_effect=ValueError('invalid id')), callback, {})
    assert '/start' in callback.answer.call_args.args[0]


@pytest.mark.asyncio
async def test_polling_reconnects_and_keeps_search_independent():
    stop = asyncio.Event()
    calls = 0
    bot = SimpleNamespace(set_my_commands=AsyncMock())
    async def poll(*args, **kwargs):
        nonlocal calls
        calls += 1
        assert kwargs['handle_signals'] is False and kwargs['close_bot_session'] is False
        if calls == 1:
            raise ConnectionError('Telegram offline')
        stop.set()
    dispatcher = SimpleNamespace(start_polling=AsyncMock(side_effect=poll))
    await asyncio.wait_for(resilient_polling(dispatcher, bot, stop, retry_seconds=0.001), timeout=2)
    assert calls == 2
    assert {cmd.command for cmd in bot.set_my_commands.call_args.args[0]} >= {'start', 'status', 'stats', 'help'}


@pytest.mark.asyncio
async def test_timezone_and_refresh_validation(ctx):
    _, repo, _, _ = ctx
    for kwargs in ({'refresh_seconds': 1}, {'utc_offset_minutes': 2000}, {'selected_region': 'invalid'}):
        with pytest.raises(ValueError):
            await repo.ui.preferences(42, **kwargs)


@pytest.mark.asyncio
async def test_actual_restart_preserves_city_preferences_and_both_city_jobs(ctx):
    db, repo, account, _ = ctx
    await enqueue(repo, account, ('ru-2', 'ru-1', 'ru-3', 'ru-9'))
    await repo.ui.preferences(42, selected_region='ru-2', utc_offset_minutes=180)
    snapshots = [(j.id, j.region, j.subnet_id) for j in await repo.queue.list()]
    url = str(db.engine.url)
    await db.close()
    restarted = Database(url)
    try:
        await restarted.init()
        repo2 = Repository(restarted.sessions)
        assert [(j.id, j.region, j.subnet_id) for j in await repo2.queue.list()] == snapshots
        pref = await repo2.ui.preferences(42)
        assert pref.selected_region == 'ru-2' and pref.utc_offset_minutes == 180
    finally:
        await restarted.close()


@pytest.mark.asyncio
async def test_v2_additive_migration_preserves_results_and_delivered_outbox(ctx):
    db, repo, account, manager = ctx
    await enqueue(repo, account)
    job = await complete(repo, account)
    found = next(i for i in await repo.queue.pending_notifications() if i.event_type == 'FOUND')
    await repo.queue.notification_result(found, True)
    async with repo.sessions() as s:
        for table in ('notification_cooldowns', 'subnet_drafts', 'resource_observations', 'job_operations', 'job_ui_states', 'user_preferences'):
            await s.execute(text(f'DROP TABLE {table}'))
        await s.execute(text('PRAGMA user_version=2'))
        await s.commit()
    await db.init()
    assert (await repo.queue.get(job.id)).status == 'completed'
    assert len(await repo.found(42)) == 1
    assert not [i for i in await repo.queue.pending_notifications() if i.event_type == 'FOUND']
    assert 'Нет данных' in (await UiScreens(repo, manager).job(42, job.id))[0]
    async with repo.sessions() as s:
        assert await s.scalar(text('PRAGMA user_version')) == 3


@pytest.mark.asyncio
@pytest.mark.parametrize('region', ['ru-1', 'ru-3', 'ru-9', 'ru-2'])
async def test_each_concrete_region_keeps_its_existing_endpoint_and_network(region):
    from app.selectel.client import SelectelClient
    client = SelectelClient(SimpleNamespace(region='ru-3', network_api_url='unused'), 'password')
    client._request = AsyncMock(return_value={'floatingip': {'id': 'mock'}})
    await client.create_floating_ip(region, subnet(region)['subnet_id'])
    call_args = client._request.call_args
    assert call_args.kwargs['region'] == region
    assert call_args.kwargs['json']['floatingip']['floating_network_id'] == REGIONS[region]['floating_network_id']
    assert call_args.kwargs['json']['floatingip']['subnet_id'] == subnet(region)['subnet_id']
    assert REGIONS[region]['network_api_url'] == f'https://{region}.cloud.api.selcloud.ru/network/v2.0'


@pytest.mark.asyncio
async def test_no_free_ip_confirms_connectivity_but_does_not_invent_success_time(ctx):
    _, repo, account, _ = ctx
    await enqueue(repo, account)
    job = await repo.queue.claim(account.id)
    await repo.queue.release(job, error='NO_FREE_IP: HTTP 409', clear_operation=True)
    assert (await repo.ui.job_state(job.id)).last_success_at is None
    assert (await repo.ui.stats(42))['success'] is None


@pytest.mark.asyncio
async def test_legacy_stop_button_never_stops_other_city_jobs(ctx):
    _, repo, account, manager = ctx
    await enqueue(repo, account, ('ru-2', 'ru-3'))
    legacy = await repo.create_task(account_id=account.id, telegram_user_id=42, region='ru-3',
        subnet_id=subnet('ru-3')['subnet_id'], subnet_cidr=subnet('ru-3')['cidr'], network_id=REGIONS['ru-3']['floating_network_id'])
    router = build_experience_router(repo, manager, {42}, AsyncMock())
    await handler(router, 'legacy_task')(call(f'task:stop:{legacy.id}'))
    assert all(j.status == 'pending' for j in await repo.queue.list())
    assert (await repo.get_account(account.id)).scheduler_status == 'RUNNING'


def scoped_manager(repo):
    from app.hunter.account_manager import BurstSchedulerManager
    manager = BurstSchedulerManager(repo, AsyncMock(), AsyncMock())
    manager.recalculate_schedule = AsyncMock()
    manager._ensure_scheduler = AsyncMock()
    return manager


@pytest.mark.asyncio
async def test_start_all_defaults_to_saved_moscow_not_account_region(ctx):
    _, repo, account, _ = ctx
    assert account.region == 'ru-3'
    await repo.ui.preferences(42, selected_region='ru-2')
    manager = scoped_manager(repo)
    assert await manager.start_all_accounts(42) == (1, [])
    jobs = await repo.queue.list(user_id=42)
    assert len(jobs) == 3
    assert {j.region for j in jobs} == {'ru-2'}
    assert {j.cidr for j in jobs} == {'5.178.85.0/24', '81.163.22.0/24', '81.163.23.0/24'}
    manager.recalculate_schedule.assert_awaited_once()


@pytest.mark.asyncio
async def test_first_single_account_start_uses_saved_city(ctx):
    _, repo, account, _ = ctx
    await repo.ui.preferences(42, selected_region='ru-2')
    await scoped_manager(repo).start_account(account.id)
    assert {j.region for j in await repo.queue.list()} == {'ru-2'}


@pytest.mark.asyncio
async def test_disabled_city_draft_does_not_pause_working_city(ctx):
    _, repo, account, manager = ctx
    await enqueue(repo, account, ('ru-3',))
    draft = await repo.ui.create_draft(42, account.id, 'ru-2', [subnet('ru-2')['subnet_id']])
    await repo.set_region_enabled('ru-2', False)
    router = build_multisubnet_router(repo, manager, {42})
    callback = call(f's:{draft.id}:save')
    await handler(router, 'draft_action')(callback)
    assert all(j.status == 'pending' and j.region == 'ru-3' for j in await repo.queue.list())
    manager.start_account.assert_not_awaited()
    assert 'выключена' in callback.answer.call_args.args[0]


@pytest.mark.asyncio
async def test_moscow_start_pauses_old_spb_preserving_ids_attempts_and_other_owners(ctx):
    _, repo, account, _ = ctx
    old = await enqueue(repo, account, ('ru-1', 'ru-3', 'ru-9'))
    second = await repo.add_account(telegram_user_id=42, display_name='second', domain='d', username='u',
        encrypted_password='encrypted', project_id='project', region='ru-3', scheduler_status='RUNNING')
    old.extend(await enqueue(repo, second, ('ru-9',)))
    in_flight = await repo.queue.claim(account.id)
    await repo.queue.begin_operation(in_flight, 'old-network')
    other = await repo.add_account(telegram_user_id=99, display_name='other', domain='d', username='u',
        encrypted_password='encrypted', project_id='project', scheduler_status='RUNNING')
    await enqueue(repo, other, ('ru-3',))
    await repo.ui.preferences(42, selected_region='ru-2')
    assert await scoped_manager(repo).start_all_accounts(42) == (2, [])
    jobs = await repo.queue.list(user_id=42)
    assert all(j.status == 'paused' for j in jobs if j.region != 'ru-2')
    assert {j.id for j in jobs if j.region != 'ru-2'} == {j.id for j in old}
    saved = await repo.queue.get(in_flight.id)
    assert saved.operation_id == in_flight.operation_id and saved.lease_token == in_flight.lease_token
    assert saved.attempts == 1 and saved.region == in_flight.region
    assert all(j.status == 'pending' for j in await repo.queue.list(user_id=99))
    assert await repo.ui.active_by_city(42) == {'ru-2': 6, 'spb': 0}


@pytest.mark.asyncio
async def test_parallel_city_start_keeps_moscow_running(ctx):
    _, repo, account, _ = ctx
    await repo.ui.preferences(42, selected_region='ru-2')
    manager = scoped_manager(repo)
    await manager.start_all_accounts(42)
    original = {j.id for j in await repo.queue.list()}
    await manager.start_all_accounts(42, selection='spb', exclusive=False)
    jobs = await repo.queue.list()
    assert original.issubset({j.id for j in jobs})
    assert all(j.status == 'pending' for j in jobs)
    assert await repo.ui.active_by_city(42) == {'ru-2': 3, 'spb': len([s for s in TARGET_SUBNETS if s['region'] != 'ru-2'])}


@pytest.mark.asyncio
async def test_no_moscow_targets_does_not_pause_existing_spb_or_report_success(ctx):
    _, repo, account, _ = ctx
    await enqueue(repo, account, ('ru-3',))
    await repo.set_region_enabled('ru-2', False)
    await repo.ui.preferences(42, selected_region='ru-2')
    manager = scoped_manager(repo)
    started, failed = await manager.start_all_accounts(42)
    assert started == 0 and len(failed) == 1
    assert all(j.status == 'pending' for j in await repo.queue.list())
    assert {j.region for j in await repo.queue.list()} == {'ru-3'}
    manager._ensure_scheduler.assert_not_awaited()


@pytest.mark.asyncio
async def test_exclusive_subset_does_not_run_deselected_subnets(ctx):
    _, repo, account, _ = ctx
    await repo.queue.enqueue(account, [t for t in TARGET_SUBNETS])
    chosen = subnet('ru-2')
    await repo.queue.enqueue(account, [chosen], exclusive=True)
    active = [j for j in await repo.queue.list() if j.status == 'pending']
    assert len(active) == 1 and active[0].subnet_id == chosen['subnet_id']
    job = await repo.queue.claim(account.id)
    assert job.id == active[0].id
    assert await repo.queue.claim(account.id) is None


@pytest.mark.asyncio
async def test_city_switch_after_status_check_fences_old_post(ctx):
    from app.db.queue import LeaseLost
    from app.hunter.durable import DurableBurst
    _, repo, account, _ = ctx
    await enqueue(repo, account, ('ru-3',))
    job = await repo.queue.claim(account.id)
    original = repo.queue.begin_operation
    async def switch_before_post(job, network_id):
        await repo.queue.enqueue(account, [subnet('ru-2')], exclusive=True)
        await original(job, network_id)
    repo.queue.begin_operation = switch_before_post
    client = SimpleNamespace(resolve_network=AsyncMock(return_value='network'), create_floating_ip=AsyncMock())
    with pytest.raises(LeaseLost):
        await DurableBurst(repo, AsyncMock()).attempt(job, account, client)
    client.create_floating_ip.assert_not_awaited()
    saved = await repo.queue.get(job.id)
    assert saved.status == 'paused' and saved.attempts == 0 and saved.operation_id is None


@pytest.mark.asyncio
async def test_already_sent_old_city_result_is_preserved_after_switch(ctx):
    _, repo, account, _ = ctx
    await enqueue(repo, account, ('ru-3',))
    job = await repo.queue.claim(account.id)
    await repo.queue.begin_operation(job, 'network')
    await repo.queue.enqueue(account, [subnet('ru-2')], exclusive=True)
    await repo.queue.complete(job, {'id': 'already-created', 'floating_ip_address': '87.228.101.5'})
    assert (await repo.queue.get(job.id)).status == 'completed'
    found = await repo.found(42)
    assert len(found) == 1 and found[0].region == 'ru-3' and found[0].floating_ip_address == '87.228.101.5'
    assert await repo.ui.active_by_city(42) == {'ru-2': 1, 'spb': 0}


@pytest.mark.asyncio
async def test_dashboard_separates_selection_from_active_city_and_list_filters(ctx):
    _, repo, account, manager = ctx
    await enqueue(repo, account, ('ru-3',))
    await repo.ui.preferences(42, selected_region='ru-2')
    screens = UiScreens(repo, manager)
    dashboard, _ = await screens.dashboard(42)
    assert 'Выбор для запуска' in dashboard and 'Москва — <b>0</b>, Петербург — <b>1</b>' in dashboard
    selected_text, selected_markup = await screens.listing(42)
    assert 'Всего в этом разделе: 0' in selected_text and '87.228.101.0/24' not in selected_text
    all_text, all_markup = await screens.listing(42, city='all')
    assert '87.228.101.0/24' in all_text
    assert 'jobs:scope:spb:0' in callbacks(selected_markup)
    assert_screen((dashboard, _))
    assert_screen((all_text, all_markup))


@pytest.mark.asyncio
async def test_city_pause_and_selected_region_survive_database_restart(ctx):
    db, repo, account, _ = ctx
    await enqueue(repo, account, ('ru-3',))
    await repo.ui.preferences(42, selected_region='ru-2')
    await scoped_manager(repo).start_all_accounts(42)
    snapshot = [(j.id, j.region, j.status) for j in await repo.queue.list()]
    url = str(db.engine.url)
    await db.close()
    restarted = Database(url)
    try:
        await restarted.init()
        repo2 = Repository(restarted.sessions)
        await scoped_manager(repo2).restore()
        assert [(j.id, j.region, j.status) for j in await repo2.queue.list()] == snapshot
        assert await repo2.ui.active_by_city(42) == {'ru-2': 3, 'spb': 0}
    finally:
        await restarted.close()
