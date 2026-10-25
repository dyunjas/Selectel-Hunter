from .presentation import (RULE, region_name, number, duration, timestamp, timezone_name,
                           keyboard, job_text, job_keyboard, friendly_error, JOB_LABELS, resource_status, regions_for)
from .formatting import safe
from .keyboards import main_menu


class UiScreens:
    def __init__(self, repo, manager):
        self.repo, self.manager = repo, manager

    async def dashboard(self, user_id):
        pref = await self.repo.ui.preferences(user_id)
        stats = await self.repo.ui.stats(user_id)
        cities = await self.repo.ui.active_by_city(user_id)
        task = getattr(self.manager, "_scheduler_task", None)
        status = "🟢 работает" if task and not task.done() else "⚪ планировщик не запущен"
        text = (f"🎯 <b>SELECTEL IP HUNTER</b>\n{RULE}\n\n"
                f"🌍 Выбор для запуска: <b>{safe(region_name(pref.selected_region))}</b>\n"
                f"Система: {status}\n📋 Активных заданий: <b>{number(stats['active'])}</b>\n"
                f"Сейчас ищут: Москва — <b>{number(cities['ru-2'])}</b>, Петербург — <b>{number(cities['spb'])}</b>\n"
                f"✅ Найдено IP: <b>{number(stats['found'])}</b>\n\n"
                f"{RULE}\n«Запустить выбранные регионы» запустит аккаунты в выбранном городе или сразу в обоих. Задания вне выбранных регионов будут на паузе.")
        return text, main_menu()

    async def listing(self, user_id, page=0, active_only=False, city=None):
        from .views import page_slice
        if city is None:
            city = (await self.repo.ui.preferences(user_id)).selected_region
        selected_regions = regions_for(city)
        if not selected_regions:
            raise ValueError("Неизвестный город")
        jobs = await self.repo.queue.list(user_id=user_id)
        jobs = [job for job in jobs if job.region in selected_regions]
        accounts = {a.id: a for a in await self.repo.accounts(user_id)}
        if active_only:
            jobs = [j for j in jobs if j.status in {"pending", "running"} and j.account_id in accounts
                    and accounts[j.account_id].scheduler_status in {"RUNNING", "RATE_LIMIT_COOLDOWN"}]
        priority = {"running": 0, "pending": 1, "failed": 2, "paused": 3, "completed": 4, "cancelled": 5}
        jobs.sort(key=lambda j: (priority.get(j.status, 6), j.created_at, j.id))
        visible, page, pages = page_slice(jobs, page, 6)
        lines = ["📋 <b>МОИ ЗАДАНИЯ</b>", RULE, f"🌍 {safe(region_name(city))}", f"Всего в этом разделе: {number(len(jobs))}", ""]
        rows = [[("🏙 Москва", "jobs:scope:ru-2:0"), ("🌉 Петербург", "jobs:scope:spb:0"), ("Все", "jobs:scope:all:0")]]
        for job in visible:
            label = JOB_LABELS.get(job.status, "Состояние неизвестно")
            if job.status in {"pending", "running"} and job.resource_json:
                label = "🔎 Проверяем предыдущий запрос"
            if job.status in {"running", "pending"} and job.account_id in accounts and accounts[job.account_id].scheduler_status in {"PAUSED", "STOPPED"}:
                label = "⏸ Аккаунт приостановлен"
            lines.append(f"<b>#{safe(job.id[:8])}</b> · {safe(region_name(job.region))}\n<code>{safe(job.cidr)}</code> · {label}\n")
            rows.append([(f"#{job.id[:8]} · {job.cidr}", f"job:view:{job.id}")])
        if not jobs:
            lines.append("В этом разделе заданий пока нет. Проверьте другой город или начните поиск.")
        if pages > 1:
            rows.append([("‹", f"jobs:scope:{city}:{max(0,page-1)}"), (f"{page+1}/{pages}", "ui:noop"), ("›", f"jobs:scope:{city}:{min(pages-1,page+1)}")])
        rows += [[("🎯 Начать поиск", "hunt:start")], [("◀️ Главное меню", "home")]]
        return "\n".join(lines), keyboard(*rows)

    async def job(self, user_id, job_id):
        job = await self.repo.queue.get(job_id)
        if not job or job.telegram_user_id != user_id:
            return None
        pref = await self.repo.ui.preferences(user_id)
        state = await self.repo.ui.job_state(job_id)
        account = await self.repo.get_account(job.account_id)
        if not account:
            return None
        position = await self.repo.ui.position(job) if job.status == "pending" else None
        return job_text(job, state, account, pref.utc_offset_minutes, position), job_keyboard(job, account)

    async def stats(self, user_id, region="all"):
        pref = await self.repo.ui.preferences(user_id)
        data = await self.repo.ui.stats(user_id, region)
        count = lambda key: number(data[key]) if data[key] is not None else "Нет данных"
        rate = f"{data['rate']:.2f} / мин." if data['rate'] is not None else "Нет данных"
        lines = ["📊 <b>СТАТИСТИКА</b>", RULE, f"<b>{safe(region_name(region))}</b>", "",
                 f"⏱ Суммарное время поиска: {duration(data['elapsed'])}",
                 f"🔄 Всего попыток: <b>{count('attempts')}</b>",
                 f"✅ Найдено IP: <b>{count('found')}</b>",
                 f"📋 Активных заданий: <b>{count('active')}</b>",
                 f"⚠️ Ошибок API: <b>{count('errors')}</b>", "",
                 f"Попыток за час: <b>{count('hour')}</b>", f"Попыток за 24 часа: <b>{count('day')}</b>",
                 f"Найдено IP за 24 часа: <b>{count('found_day')}</b>",
                 f"Средняя скорость: {rate}",
                 f"Последняя успешная операция:\n{timestamp(data['success'], pref.utc_offset_minutes)}", "",
                 "Всего попыток — счётчики сохранённых заданий. Часовые показатели и время поиска — по наблюдениям с обновления. Для отсутствующих данных показано «Нет данных».",
                 f"Время: {timezone_name(pref.utc_offset_minutes)}"]
        return "\n".join(lines), keyboard([("🏙 Москва", "ui:stats:ru-2"), ("🌉 Петербург", "ui:stats:spb")],
            [("Все регионы", "ui:stats:all"), ("🔄 Обновить", f"ui:stats:{region}")], [("◀️ Главное меню", "home")])

    async def history(self, user_id, region="all", job_id=None, page=0):
        pref = await self.repo.ui.preferences(user_id)
        items, page, pages, count = await self.repo.ui.history(user_id, region, job_id, page)
        lines = ["📋 <b>ИСТОРИЯ НАЙДЕННЫХ IP</b>", RULE, f"Находок: <b>{number(count)}</b>", ""]
        rows = []
        for item in items:
            job = await self.repo.ui.found_job(item)
            observation = await self.repo.ui.observation(item.id)
            lines.extend([f"✅ <code>{safe(item.floating_ip_address)}</code>",
                          f"{safe(region_name(item.region))} · <code>{safe(item.subnet_cidr)}</code>",
                          f"{timestamp(item.found_at, pref.utc_offset_minutes)} · #{safe(job.id[:8] if job else str(item.hunter_task_id))}",
                          f"UUID: <code>{safe(item.floating_ip_id or 'Нет данных')}</code>",
                          f"Статус: {safe(resource_status(observation.status)) if observation else 'Сейчас не проверялся'}", ""])
            rows.append([(f"📋 {item.floating_ip_address}", f"ip:view:{item.id}")])
        if not items:
            lines.append("Находок пока нет. Подтверждённые результаты появятся здесь.")
        prefix = f"history:job:{job_id}" if job_id else f"history:region:{region}"
        if pages > 1:
            rows.append([("‹", f"{prefix}:{max(0,page-1)}"), (f"{page+1}/{pages}", "ui:noop"), ("›", f"{prefix}:{min(pages-1,page+1)}")])
        if not job_id:
            rows.append([("🏙 Москва", "history:region:ru-2:0"), ("🌉 Петербург", "history:region:spb:0"), ("Все", "history:region:all:0")])
        rows.append([("◀️ Назад", f"job:view:{job_id}" if job_id else "home")])
        return "\n".join(lines), keyboard(*rows)

    async def ip(self, user_id, found_id):
        item = await self.repo.ui.found_item(user_id, found_id=found_id)
        if not item:
            return None
        pref = await self.repo.ui.preferences(user_id)
        job = await self.repo.ui.found_job(item)
        observation = await self.repo.ui.observation(item.id)
        lines = ["📋 <b>ИНФОРМАЦИЯ ОБ IP</b>", RULE, f"✅ <code>{safe(item.floating_ip_address)}</code>", "",
                 f"🌍 {safe(region_name(item.region))}", f"Подсеть: <code>{safe(item.subnet_cidr)}</code>",
                 f"Находка: {timestamp(item.found_at, pref.utc_offset_minutes)}",
                 f"Задание: #{safe(job.id[:8] if job else str(item.hunter_task_id))}",
                 f"Floating IP UUID:\n<code>{safe(item.floating_ip_id or 'Нет данных')}</code>", "",
                 f"Подтверждённый статус: {safe(resource_status(observation.status)) if observation else 'Текущее состояние не проверялось'}",
                 f"Проверено: {timestamp(observation.checked_at if observation else None, pref.utc_offset_minutes)}",
                 "При находке выделение IP было подтверждено Selectel и сохранено в БД."]
        rows = [[("🔄 Проверить в Selectel", f"ip:check:{item.id}")]]
        if job:
            rows.append([("🎯 Открыть задание", f"job:view:{job.id}")])
        rows.append([("◀️ История IP", "found:list")])
        return "\n".join(lines), keyboard(*rows)

    async def settings(self, user_id):
        pref = await self.repo.ui.preferences(user_id)
        return (f"⚙️ <b>НАСТРОЙКИ</b>\n{RULE}\n\n"
                f"Живая карточка: каждые <b>{pref.refresh_seconds} сек.</b>\n"
                f"Часовой пояс: <b>{timezone_name(pref.utc_offset_minutes)}</b>\n\nВыберите параметр:",
                keyboard([("15 сек.", "ui:refresh:15"), ("20 сек.", "ui:refresh:20"), ("30 сек.", "ui:refresh:30")],
                         [("UTC+0", "ui:timezone:0"), ("UTC+3", "ui:timezone:180"), ("UTC+5", "ui:timezone:300")],
                         [("🔔 Уведомления", "notifications:global"), ("⚙️ Поиск", "scheduler:settings")],
                         [("🎯 Общие подсети", "targets:view")],
                         [("👤 Аккаунты", "account:list"), ("🌍 Ограничения регионов", "regions:view")],
                         [("◀️ Главное меню", "home")]))
