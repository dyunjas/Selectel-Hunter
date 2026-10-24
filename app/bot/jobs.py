from aiogram import Router, F
from .multisubnet import AdminOnlyMiddleware
from .views import edit_screen
from .presentation import keyboard, safe
from .screens import UiScreens


def build_jobs_router(repo, manager, admin_ids, live=None, screens=None):
    router = Router()
    router.callback_query.outer_middleware(AdminOnlyMiddleware(admin_ids, allow_topics=True))
    screens = screens or UiScreens(repo, manager)

    @router.callback_query(F.data == "task:list")
    @router.callback_query(F.data.regexp(r"^jobs:page:\d+$"))
    @router.callback_query(F.data.regexp(r"^jobs:scope:(all|spb|ru-\d+):\d+$"))
    async def listing(call):
        page = int(call.data.rsplit(":", 1)[-1]) if call.data.startswith(("jobs:page:", "jobs:scope:")) else 0
        city = call.data.split(":")[2] if call.data.startswith("jobs:scope:") else None
        await edit_screen(call.message, *(await screens.listing(call.from_user.id, page, city=city)))
        await call.answer()

    @router.callback_query(F.data.regexp(r"^job:(view|pause|resume|stop|confirm):[a-f0-9]{32}$"))
    async def job_action(call):
        _, action, job_id = call.data.split(":")
        job = await repo.queue.get(job_id)
        if not job or job.telegram_user_id != call.from_user.id:
            await call.answer("Задание не найдено или больше недоступно", show_alert=True)
            return
        if action == "stop":
            if job.status == "completed":
                await call.answer("Задание уже завершено, IP сохранён")
                return
            await edit_screen(call.message, f"🛑 <b>ОСТАНОВИТЬ ПОИСК?</b>\n\nЗадание #{safe(job.id[:8])}\nНайденные IP и история сохранятся. Уже отправленный запрос может завершиться.",
                keyboard([("🛑 Да, остановить", f"job:confirm:{job.id}")], [("◀️ К заданию", f"job:view:{job.id}")]))
            await call.answer()
            return
        transitions = {"pause": "paused", "resume": "pending", "confirm": "cancelled"}
        if action in transitions:
            changed = await repo.queue.control(job.id, call.from_user.id, transitions[action])
            if not changed:
                await call.answer("Задание уже завершено или больше недоступно")
                return
            if action == "resume" and changed:
                await manager.resume_account(job.account_id)
        rendered = await screens.job(call.from_user.id, job_id)
        if rendered:
            await edit_screen(call.message, *rendered)
            if live:
                pref = await repo.ui.preferences(call.from_user.id)
                live.watch(call.message, call.from_user.id, job_id, rendered, pref.refresh_seconds)
        await call.answer({"pause": "Поиск приостановлен", "resume": "Поиск возобновлён", "confirm": "Поиск остановлен"}.get(action, None))

    @router.callback_query(F.data.startswith("job:"))
    async def stale_job(call):
        await call.answer("Кнопка устарела. Откройте «Мои задания» через /start.", show_alert=True)
    return router
