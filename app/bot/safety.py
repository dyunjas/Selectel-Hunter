import logging
from aiogram import BaseMiddleware, Router
from aiogram.exceptions import TelegramBadRequest, TelegramForbiddenError, TelegramRetryAfter
from sqlalchemy.exc import SQLAlchemyError
from .multisubnet import AdminOnlyMiddleware

log = logging.getLogger(__name__)


class UiErrorBoundary(BaseMiddleware):
    async def __call__(self, handler, event, data):
        try:
            message = getattr(event, "message", None)
            if message is None or not hasattr(message, "edit_text"):
                await event.answer("Сообщение недоступно. Откройте меню через /start.", show_alert=True)
                return
            return await handler(event, data)
        except TelegramRetryAfter:
            text = "Telegram просит сделать паузу. Повторите действие чуть позже."
        except TelegramForbiddenError:
            return None
        except TelegramBadRequest as exc:
            if "message is not modified" in exc.message.lower():
                return None
            text = "Сообщение больше недоступно. Откройте актуальное меню через /start."
        except (ValueError, KeyError, IndexError):
            text = "Кнопка устарела. Откройте актуальное меню через /start."
        except SQLAlchemyError:
            log.exception("UI database request failed")
            text = "Сейчас не удалось загрузить данные. Повторите действие позже."
        try:
            await event.answer(text, show_alert=True)
        except (TelegramBadRequest, TelegramRetryAfter, TelegramForbiddenError):
            pass


def build_stale_router(admin_ids):
    router = Router()
    router.callback_query.outer_middleware(AdminOnlyMiddleware(admin_ids, allow_topics=True))

    @router.callback_query()
    async def stale(call):
        await call.answer("Кнопка устарела. Откройте меню через /start — задания сохранены.", show_alert=True)
    return router
