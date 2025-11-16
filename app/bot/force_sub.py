from __future__ import annotations

from aiogram import BaseMiddleware, Bot
from aiogram.types import InlineKeyboardMarkup, InlineKeyboardButton, Message, CallbackQuery
from aiogram.enums import ParseMode

from app.core.config import settings


async def _check_joined(bot: Bot, user_id: int, channels: list[str]) -> tuple[bool, list[str]]:
    not_joined: list[str] = []
    for ch in channels:
        try:
            member = await bot.get_chat_member(chat_id=ch, user_id=user_id)
            status = str(getattr(member, "status", ""))
            if status not in {"creator", "administrator", "member"}:
                not_joined.append(ch)
        except Exception:
            not_joined.append(ch)
    return len(not_joined) == 0, not_joined


def _build_keyboard(channels: list[str]) -> InlineKeyboardMarkup:
    rows: list[list[InlineKeyboardButton]] = []
    row: list[InlineKeyboardButton] = []
    for idx, ch in enumerate(channels, 1):
        username = ch.lstrip("@")
        url = f"https://t.me/{username}"
        text = f"Jᴏɪɴ {idx}"
        row.append(InlineKeyboardButton(text=text, url=url))
        if len(row) == 2:
            rows.append(row)
            row = []
    if row:
        rows.append(row)
    rows.append([InlineKeyboardButton(text="💡 Jᴏɪɴᴇᴅ 💡", callback_data="force_sub:check")])
    return InlineKeyboardMarkup(inline_keyboard=rows)


async def send_force_sub(event: Message | CallbackQuery, bot: Bot) -> None:
    channels = settings.FORCE_SUB_CHATS or []
    text = "<b>Verification required</b>\nJoin all required channels to use the bot."
    if isinstance(event, Message):
        await event.answer(text, parse_mode=ParseMode.HTML, reply_markup=_build_keyboard(channels))
    elif isinstance(event, CallbackQuery):
        try:
            await event.answer()
        except Exception:
            pass
        await event.message.answer(text, parse_mode=ParseMode.HTML, reply_markup=_build_keyboard(channels))


class ForceSubMiddleware(BaseMiddleware):
    async def __call__(self, handler, event, data):
        channels = settings.FORCE_SUB_CHATS or []
        if not channels:
            return await handler(event, data)
        user = None
        if isinstance(event, Message):
            user = event.from_user
        elif isinstance(event, CallbackQuery):
            user = event.from_user
            if event.data == "force_sub:check":
                return await handler(event, data)
        if not user:
            return await handler(event, data)
        if user.id in settings.ADMIN_IDS:
            return await handler(event, data)
        bot: Bot = data.get("bot")
        ok, _ = await _check_joined(bot, user.id, channels)
        if ok:
            return await handler(event, data)
        await send_force_sub(event, bot)
        return


async def force_sub_check(cb: CallbackQuery, bot: Bot) -> None:
    channels = settings.FORCE_SUB_CHATS or []
    ok, _ = await _check_joined(bot, cb.from_user.id, channels)
    if ok:
        try:
            await cb.answer("Verified")
        except Exception:
            pass
        try:
            await cb.message.delete()
        except Exception:
            pass
        await cb.message.answer("✅ Verified. Send /start to open the menu.")
        return
    try:
        await cb.answer("Not yet. Join all and try again.")
    except Exception:
        pass
    await cb.message.answer(
        "<b>Still incomplete</b>\nPlease join all channels and press the button again.",
        parse_mode=ParseMode.HTML,
        reply_markup=_build_keyboard(channels),
    )
