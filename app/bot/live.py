"""One bounded, independent refresh loop for all open job messages."""
import asyncio
import logging
import time
import weakref
from dataclasses import dataclass
from aiogram import BaseMiddleware
from aiogram.exceptions import TelegramBadRequest, TelegramForbiddenError, TelegramRetryAfter

log = logging.getLogger(__name__)


@dataclass
class Subscription:
    user_id: int
    job_id: str
    text: str
    markup: str
    next_at: float
    expires_at: float


class LiveCards(BaseMiddleware):
    def __init__(self, bot, screens, max_cards=200):
        self.bot, self.screens, self.max_cards = bot, screens, max_cards
        self.cards = {}
        self.blocked_until = 0.0
        self._locks = weakref.WeakValueDictionary()

    def message_lock(self, key):
        lock = self._locks.get(key)
        if lock is None:
            lock = asyncio.Lock()
            self._locks[key] = lock
        return lock

    def watch(self, message, user_id, job_id, rendered, interval):
        key = (message.chat.id, message.message_id)
        for previous in list(self.cards):
            if self.cards[previous].user_id == user_id:
                self.cards.pop(previous, None)
        while len(self.cards) >= self.max_cards:
            self.cards.pop(next(iter(self.cards)))
        self.cards[key] = Subscription(user_id, job_id, rendered[0], rendered[1].model_dump_json(),
                                       time.monotonic() + interval, time.monotonic() + 86400)

    async def __call__(self, handler, event, data):
        message = getattr(event, "message", None)
        if message:
            key = (message.chat.id, message.message_id)
            async with self.message_lock(key):
                self.cards.pop(key, None)
                return await handler(event, data)
        elif getattr(event, "from_user", None):
            for key in list(self.cards):
                if self.cards[key].user_id == event.from_user.id:
                    self.cards.pop(key, None)
        return await handler(event, data)

    async def tick(self):
        at = time.monotonic()
        if at < self.blocked_until:
            return
        for key, card in list(self.cards.items()):
            if self.cards.get(key) is not card:
                continue
            if at >= card.expires_at:
                self.cards.pop(key, None)
                continue
            if card.next_at > at:
                continue
            pref = await self.screens.repo.ui.preferences(card.user_id)
            card.next_at = at + pref.refresh_seconds
            rendered = await self.screens.job(card.user_id, card.job_id)
            if rendered is None:
                self.cards.pop(key, None)
                continue
            text, markup = rendered
            serialized = markup.model_dump_json()
            if (text, serialized) == (card.text, card.markup):
                continue
            if self.cards.get(key) is not card:
                continue
            try:
                async with self.message_lock(key):
                    if self.cards.get(key) is not card:
                        continue
                    await self.bot.edit_message_text(chat_id=key[0], message_id=key[1], text=text, reply_markup=markup)
                    card.text, card.markup = text, serialized
            except TelegramRetryAfter as exc:
                self.blocked_until = at + exc.retry_after
                card.next_at = self.blocked_until
            except TelegramBadRequest as exc:
                if "message is not modified" in exc.message.lower():
                    card.text, card.markup = text, serialized
                else:
                    self.cards.pop(key, None)
            except TelegramForbiddenError:
                self.cards.pop(key, None)
            except Exception:
                card.next_at = at + max(30, pref.refresh_seconds)
                log.warning("Live card refresh deferred user=%s", card.user_id)
            self.blocked_until = max(self.blocked_until, at + 1)
            return

    async def run(self):
        while True:
            try:
                await self.tick()
            except asyncio.CancelledError:
                raise
            except Exception:
                log.exception("Live card iteration failed")
            await asyncio.sleep(0.5)
