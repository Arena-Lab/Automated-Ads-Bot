from __future__ import annotations
import asyncio
import logging
from datetime import datetime, timezone
import os
from aiogram import Bot, Dispatcher, F
from aiogram.types import Message, CallbackQuery, InputMediaPhoto
from aiogram.enums import ParseMode
from aiogram.client.default import DefaultBotProperties
from aiogram.filters import CommandStart, Command
from aiogram.exceptions import TelegramBadRequest
from app.core.config import settings
from app.core.db import init_db, get_db_sync
from app.core.session_manager import session_manager
from app.core.telegram_login import telegram_login_manager
from .keyboards import (
    main_menu_kb, back_to_menu_kb, targets_menu_kb, interval_menu_kb,
    login_menu_kb, accounts_menu_kb, account_detail_kb, analytics_kb
)
from arq import create_pool
from arq.connections import RedisSettings
from urllib.parse import urlparse
import ssl as _ssl
from bson import ObjectId


# Local logger
logger = logging.getLogger(__name__)

async def get_runtime_config_value(key: str, default):
    db = get_db_sync()
    doc = await db.config.find_one({"_id": "runtime"})
    if not doc:
        return default
    return doc.get(key, default)


async def get_presets() -> dict:
    return {
        "safe": int(await get_runtime_config_value("INTERVAL_PRESETS_SAFE", settings.INTERVAL_PRESETS_SAFE)),
        "default": int(await get_runtime_config_value("INTERVAL_PRESETS_DEFAULT", settings.INTERVAL_PRESETS_DEFAULT)),
        "aggressive": int(await get_runtime_config_value("INTERVAL_PRESETS_AGGRESSIVE", settings.INTERVAL_PRESETS_AGGRESSIVE)),
    }

logger = logging.getLogger(__name__)
arq_pool = None  # will be initialized on startup

def _to_bool(v) -> bool:
    if isinstance(v, bool):
        return v
    if isinstance(v, (int, float)):
        return v != 0
    if isinstance(v, str):
        s = v.strip().lower()
        return s in {"1", "true", "on", "yes", "y"}
    return bool(v)


async def safe_answer_callback(cb: CallbackQuery, text: str = None, show_alert: bool = False):
    """Safely answer callback query, ignoring expired queries"""
    try:
        await cb.answer(text, show_alert=show_alert)
    except TelegramBadRequest as e:
        if "query is too old" in str(e) or "query ID is invalid" in str(e):
            # Callback query expired, ignore silently
            pass
        else:
            # Re-raise other errors
            raise e


def hero_caption() -> str:
    return (
        f"<b>{settings.BOT_DISPLAY_NAME}</b>\n"
        f"Manage high-scale ads with safe rate limits.\n\n"
        f"Use the buttons below to verify, set message, targets, intervals, start/stop, analytics, and auto-replies."
    )


async def cleanup_expired_sessions():
    """Periodic cleanup of expired login sessions"""
    while True:
        try:
            await telegram_login_manager.cleanup_expired_sessions()
            await asyncio.sleep(300)  # Run every 5 minutes
        except Exception as e:
            logger.error(f"Error in cleanup task: {e}")
            await asyncio.sleep(60)  # Wait 1 minute on error


async def on_startup(bot: Bot):
    await init_db()
    me = await bot.get_me()
    logger.info("Bot started as @%s", me.username)
    
    # Start cleanup task
    asyncio.create_task(cleanup_expired_sessions())
    
    # init ARQ pool
    global arq_pool
    _u = urlparse(settings.REDIS_URL)
    _db = 0
    if _u.path and len(_u.path) > 1:
        try:
            _db = int(_u.path.lstrip('/').split('/')[0])
        except Exception:
            _db = 0
    _use_ssl = _u.scheme == 'rediss'
    _ssl_reqs = None
    if 'ssl_cert_reqs=none' in (_u.query or '').lower():
        _ssl_reqs = _ssl.CERT_NONE
    arq_pool = await create_pool(RedisSettings(
        host=_u.hostname or 'localhost',
        port=_u.port or 6379,
        password=_u.password,
        database=_db,
        ssl=_use_ssl,
        ssl_cert_reqs=_ssl_reqs,
    ))


async def start_handler(message: Message, bot: Bot):
    if settings.START_MEDIA_URL:
        try:
            await message.answer_photo(
                photo=settings.START_MEDIA_URL,
                caption=hero_caption(),
                parse_mode=ParseMode.HTML,
                reply_markup=main_menu_kb(campaign_running=False),
            )
            return
        except Exception as e:
            logger.warning("Failed to send start media: %s", e)
    await message.answer(hero_caption(), parse_mode=ParseMode.HTML, reply_markup=main_menu_kb(campaign_running=False))


async def menu_home(cb: CallbackQuery):
    # Check if user has active campaign
    db = get_db_sync()
    user = await db.users.find_one({"user_id": cb.from_user.id})
    active_id = (user or {}).get("active_campaign_id")
    
    # Verify if campaign is actually running
    campaign_running = False
    if active_id:
        try:
            oid = ObjectId(active_id)
            campaign = await db.campaigns.find_one({"_id": oid})
            if campaign and campaign.get("status") == "running":
                campaign_running = True
            else:
                # Clean up stale campaign ID
                await db.users.update_one({"user_id": cb.from_user.id}, {"$unset": {"active_campaign_id": 1}})
        except Exception:
            # Invalid campaign ID, clean it up
            await db.users.update_one({"user_id": cb.from_user.id}, {"$unset": {"active_campaign_id": 1}})
    
    # Use appropriate caption based on campaign status
    if campaign_running:
        caption = (
            f"<b>{settings.BOT_DISPLAY_NAME}</b>\n"
            f"🎯 <b>CAMPAIGN RUNNING</b> 🎯\n\n"
            f"📊 Messages are being sent automatically\n\n"
            f"Use 'Stop Campaign' to stop or 'Analytics' to monitor."
        )
    else:
        caption = hero_caption()
    
    try:
        if cb.message.photo:
            try:
                await cb.message.edit_media(
                    InputMediaPhoto(media=settings.START_MEDIA_URL, caption=caption, parse_mode=ParseMode.HTML),
                    reply_markup=main_menu_kb(campaign_running),
                )
            except Exception:
                await cb.message.edit_caption(caption=caption, parse_mode=ParseMode.HTML, reply_markup=main_menu_kb(campaign_running))
        else:
            await cb.message.edit_text(caption, parse_mode=ParseMode.HTML, reply_markup=main_menu_kb(campaign_running))
    except TelegramBadRequest as e:
        if "message is not modified" in str(e):
            # Message content is identical, just answer the callback
            pass
        else:
            # Re-raise other telegram errors
            raise e
    
    await safe_answer_callback(cb)


async def interval_preset(cb: CallbackQuery):
    preset = cb.data.split(":")[-1]
    presets = await get_presets()
    value = presets.get(preset, presets["default"])
    db = get_db_sync()
    await db.users.update_one({"user_id": cb.from_user.id}, {"$set": {"config.rate_per_min": int(value)}}, upsert=True)
    txt = f"<b>Interval</b> set to {value}/min."
    try:
        await cb.message.edit_caption(caption=txt, parse_mode=ParseMode.HTML, reply_markup=back_to_menu_kb())
    except Exception:
        await cb.message.edit_text(txt, parse_mode=ParseMode.HTML, reply_markup=back_to_menu_kb())
    await safe_answer_callback(cb)


async def interval_custom(cb: CallbackQuery):
    db = get_db_sync()
    await db.users.update_one({"user_id": cb.from_user.id}, {"$set": {"state": "await_custom_rate"}}, upsert=True)
    txt = "<b>Custom Interval</b>\nSend the number of messages per minute per account."
    try:
        await cb.message.edit_caption(caption=txt, parse_mode=ParseMode.HTML, reply_markup=back_to_menu_kb())
    except Exception:
        await cb.message.edit_text(txt, parse_mode=ParseMode.HTML, reply_markup=back_to_menu_kb())
    await safe_answer_callback(cb)


async def targets_include(cb: CallbackQuery):
    db = get_db_sync()
    await db.users.update_one({"user_id": cb.from_user.id}, {"$set": {"state": "await_include_ids"}}, upsert=True)
    txt = (
        "<b>Targets: Only These IDs</b>\n"
        "Send a comma-separated list of chat IDs (e.g., -100123,-100456)."
    )
    try:
        await cb.message.edit_caption(caption=txt, parse_mode=ParseMode.HTML, reply_markup=back_to_menu_kb())
    except Exception:
        await cb.message.edit_text(txt, parse_mode=ParseMode.HTML, reply_markup=back_to_menu_kb())
    await safe_answer_callback(cb)


async def targets_all(cb: CallbackQuery):
    db = get_db_sync()
    await db.users.update_one({"user_id": cb.from_user.id}, {"$set": {"config.targets.mode": "all"}}, upsert=True)
    txt = "<b>Targets</b> set to All Dialogs."
    try:
        await cb.message.edit_caption(caption=txt, parse_mode=ParseMode.HTML, reply_markup=back_to_menu_kb())
    except Exception:
        await cb.message.edit_text(txt, parse_mode=ParseMode.HTML, reply_markup=back_to_menu_kb())
    await safe_answer_callback(cb)


async def targets_exclude(cb: CallbackQuery):
    db = get_db_sync()
    await db.users.update_one({"user_id": cb.from_user.id}, {"$set": {"state": "await_exclude_ids"}}, upsert=True)
    txt = (
        "<b>Targets: Exclude IDs</b>\n"
        "Send a comma-separated list of chat IDs to exclude."
    )
    try:
        await cb.message.edit_caption(caption=txt, parse_mode=ParseMode.HTML, reply_markup=back_to_menu_kb())
    except Exception:
        await cb.message.edit_text(txt, parse_mode=ParseMode.HTML, reply_markup=back_to_menu_kb())
    await safe_answer_callback(cb)


async def menu_set_msg(cb: CallbackQuery):
    text = (
        "<b>Set Message</b>\n"
        "Forward or send the message you want to advertise to this chat.\n"
        "We will preserve formatting, entities, media, and inline buttons.\n\n"
        "Please send now."
    )
    db = get_db_sync()
    await db.users.update_one({"user_id": cb.from_user.id}, {"$set": {"state": "await_ad_message"}}, upsert=True)
    if cb.message.photo:
        await cb.message.edit_caption(caption=text, parse_mode=ParseMode.HTML, reply_markup=back_to_menu_kb())
    else:
        await cb.message.edit_text(text, parse_mode=ParseMode.HTML, reply_markup=back_to_menu_kb())
    await safe_answer_callback(cb)


async def menu_view_msg(cb: CallbackQuery):
    """View the currently set message"""
    db = get_db_sync()
    user = await db.users.find_one({"user_id": cb.from_user.id})
    cfg = (user or {}).get("config", {})
    message_payload = cfg.get("message")
    
    if not message_payload:
        text = (
            "<b>👁️ View Message</b>\n\n"
            "❌ <b>No message set!</b>\n\n"
            "Use 'Set Message' to configure your broadcast message first."
        )
    else:
        text = "<b>👁️ Current Message:</b>\n\n"
        
        # Show message details
        msg_text = message_payload.get("text", "")
        if msg_text:
            text += f"📝 <b>Text:</b>\n<code>{msg_text[:200]}{'...' if len(msg_text) > 200 else ''}</code>\n\n"
        
        media = message_payload.get("media")
        if media:
            text += f"🖼️ <b>Media:</b> {media.get('type', 'unknown').title()}\n"
            if media.get('path'):
                text += f"📁 <b>File:</b> {media['path']}\n\n"
        
        buttons = message_payload.get("buttons", [])
        if buttons:
            text += f"🔘 <b>Buttons:</b> {len(buttons)} rows\n"
            for i, row in enumerate(buttons[:3]):  # Show first 3 rows
                for btn in row:
                    text += f"  • {btn.get('text', 'No text')} → {btn.get('url', 'No URL')}\n"
            if len(buttons) > 3:
                text += f"  ... and {len(buttons) - 3} more rows\n"
            text += "\n"
        
        # Show raw data for debugging
        text += f"🔧 <b>Debug Info:</b>\n"
        text += f"Keys: {list(message_payload.keys())}\n"
        text += f"Size: {len(str(message_payload))} chars"
    
    if cb.message.photo:
        await cb.message.edit_caption(caption=text, parse_mode=ParseMode.HTML, reply_markup=back_to_menu_kb())
    else:
        await cb.message.edit_text(text, parse_mode=ParseMode.HTML, reply_markup=back_to_menu_kb())
    await safe_answer_callback(cb)


async def menu_interval(cb: CallbackQuery):
    presets = await get_presets()
    text = (
        "<b>Intervals</b>\n"
        f"Safe: {presets['safe']}/min, Default: {presets['default']}/min, Aggressive: {presets['aggressive']}/min.\n\n"
        "Pick a preset or set custom."
    )
    if cb.message.photo:
        await cb.message.edit_caption(caption=text, parse_mode=ParseMode.HTML, reply_markup=interval_menu_kb())
    else:
        await cb.message.edit_text(text, parse_mode=ParseMode.HTML, reply_markup=interval_menu_kb())
    await safe_answer_callback(cb)


async def menu_targets(cb: CallbackQuery):
    text = (
        "<b>Targets</b>\n"
        "Select targeting mode or configure include/exclude IDs.\n\n"
        "• Only These IDs: send only to provided chat IDs\n"
        "• All Dialogs: send to all dialogs of your accounts\n"
        "• Exclude IDs: skip specific IDs even in All mode\n\n"
        "Toggle where to send: Personal / Groups / Supergroups / Channels"
    )
    db = get_db_sync()
    user = await db.users.find_one({"user_id": cb.from_user.id})
    targets_cfg = (user or {}).get("config", {}).get("targets", {})
    _defaults = {"private": True, "group": True, "supergroup": True, "channel": True}
    _raw = targets_cfg.get("types") or {}
    types = {**_defaults, **{k: _to_bool(v) for k, v in _raw.items()}}
    await db.users.update_one({"user_id": cb.from_user.id}, {"$set": {"config.targets.types": types}}, upsert=True)
    if cb.message.photo:
        await cb.message.edit_caption(caption=text, parse_mode=ParseMode.HTML, reply_markup=targets_menu_kb(types))
    else:
        await cb.message.edit_text(text, parse_mode=ParseMode.HTML, reply_markup=targets_menu_kb(types))
    await safe_answer_callback(cb)


async def targets_type_toggle(cb: CallbackQuery):
    """Toggle enabled chat types for targeting and refresh the menu."""
    _, _, chat_type = cb.data.split(":", 2)
    db = get_db_sync()
    user = await db.users.find_one({"user_id": cb.from_user.id})
    cfg = (user or {}).get("config", {})
    targets_cfg = cfg.get("targets", {})
    _defaults = {"private": True, "group": True, "supergroup": True, "channel": True}
    _raw = targets_cfg.get("types") or {}
    types = {**_defaults, **{k: bool(v) for k, v in _raw.items()}}
    current = bool(types.get(chat_type, True))
    types[chat_type] = not current
    # Persist the entire merged dict so reads are consistent everywhere
    await db.users.update_one(
        {"user_id": cb.from_user.id},
        {"$set": {"config.targets.types": types}},
        upsert=True,
    )
    # Re-open the targets menu to reflect the new toggles
    await menu_targets(cb)


async def menu_start(cb: CallbackQuery):
    """Start ads campaign (only starts, doesn't stop)"""
    db = get_db_sync()
    user = await db.users.find_one({"user_id": cb.from_user.id})
    active_id = (user or {}).get("active_campaign_id")
    
    # Verify if campaign is actually running
    campaign_running = False
    if active_id:
        try:
            oid = ObjectId(active_id)
            campaign = await db.campaigns.find_one({"_id": oid})
            if campaign and campaign.get("status") == "running":
                campaign_running = True
            else:
                # Clean up stale campaign ID
                await db.users.update_one({"user_id": cb.from_user.id}, {"$unset": {"active_campaign_id": 1}})
        except Exception:
            # Invalid campaign ID, clean it up
            await db.users.update_one({"user_id": cb.from_user.id}, {"$unset": {"active_campaign_id": 1}})
    
    # If campaign is actually running, show message
    if campaign_running:
        await safe_answer_callback(cb, "⚠️ Campaign is already running! Use 'Stop Campaign' to stop it first.", show_alert=True)
        return
    
    # Start new campaign - VALIDATE ALL REQUIREMENTS
    cfg = (user or {}).get("config", {})
    message_payload = cfg.get("message")
    targets_cfg = cfg.get("targets", {})
    presets = await get_presets()
    rate = int(cfg.get("rate_per_min", presets["default"]))
    
    # COMPREHENSIVE VALIDATION
    validation_errors = []
    
    # 1. Check message
    if not message_payload:
        validation_errors.append("❌ No message set! Use 'Set Message' first.")
    elif not message_payload.get("text") and not message_payload.get("media"):
        validation_errors.append("❌ Message is empty! Set a proper message.")
    
    # 2. Check targets
    mode = targets_cfg.get("mode", "include")
    if mode == "include":
        include_ids = targets_cfg.get("include", [])
        if not include_ids:
            validation_errors.append("❌ No target IDs set! Use 'Targets' → 'Send All Chats' or add specific IDs.")
    _defaults_types = {"private": True, "group": True, "supergroup": True, "channel": True}
    _raw_types = targets_cfg.get("types") or {}
    types_cfg_val = {**_defaults_types, **{k: _to_bool(v) for k, v in _raw_types.items()}}
    if not any(types_cfg_val.values()):
        validation_errors.append("❌ All chat types are disabled! Enable at least one in 'Targets'.")
    
    # 3. Check accounts
    account_count = await session_manager.get_account_count(cb.from_user.id)
    if account_count == 0:
        validation_errors.append("❌ No accounts logged in! Use 'Login' button first.")
    
    # If there are validation errors, show them
    if validation_errors:
        error_text = "<b>⚠️ Cannot Start Campaign</b>\n\n" + "\n".join(validation_errors)
        error_text += "\n\n💡 <i>Fix these issues and try again</i>"
        await safe_answer_callback(cb, error_text, show_alert=True)
        return
    
    mode = targets_cfg.get("mode", "include")
    include = targets_cfg.get("include", [])
    exclude = targets_cfg.get("exclude", [])
    types_cfg = {**_defaults_types, **{k: _to_bool(v) for k, v in _raw_types.items()}}
    campaign = {
        "owner_user_id": cb.from_user.id,
        "message": message_payload,
        "targets": include if mode == "include" else [],
        "exclude": exclude,
        "mode": mode,
        "rate_per_min": rate,
        "types": types_cfg,
        "status": "running",
        "created_at": datetime.now(timezone.utc),
    }
    res = await db.campaigns.insert_one(campaign)
    cid = str(res.inserted_id)
    await db.users.update_one({"user_id": cb.from_user.id}, {"$set": {"active_campaign_id": cid}})
    
    # Check if user has any logged-in accounts before starting
    account_count = await session_manager.get_account_count(cb.from_user.id)
    if account_count == 0:
        await safe_answer_callback(cb, "❌ No accounts logged in! Please login first using the 'Login' button.", show_alert=True)
        # Clean up the campaign we just created
        await db.campaigns.delete_one({"_id": res.inserted_id})
        await db.users.update_one({"user_id": cb.from_user.id}, {"$unset": {"active_campaign_id": 1}})
        return
    
    # clear old analytics
    try:
        await db.logs.delete_many({"owner_user_id": cb.from_user.id})
    except Exception:
        pass
    # enqueue worker
    try:
        await arq_pool.enqueue_job("send_campaign", cid)
    except Exception as e:
        await safe_answer_callback(cb, f"Queue error: {e}", show_alert=True)
        return
    
    await safe_answer_callback(cb, "✅ Ads Started Successfully! Campaign is now running.", show_alert=True)
    
    types_line = (
        f"Personal: {'ON' if types_cfg.get('private', True) else 'OFF'} | "
        f"Groups: {'ON' if types_cfg.get('group', True) else 'OFF'} | "
        f"Supergroups: {'ON' if types_cfg.get('supergroup', True) else 'OFF'} | "
        f"Channels: {'ON' if types_cfg.get('channel', True) else 'OFF'}"
    )
    include_count = len(include) if mode == "include" else 0
    exclude_count = len(exclude)
    sample_ids = ", ".join(str(x) for x in include[:5]) if mode == "include" and include else "-"
    msg_text = (message_payload or {}).get("text", "")
    msg_preview = (msg_text[:120] + ("..." if len(msg_text) > 120 else "")) if msg_text else "(no text)"
    media_info = (message_payload or {}).get("media")
    media_line = media_info.get("type") if media_info else "None"
    buttons_count = len((message_payload or {}).get("buttons", []))
    updated_text = (
        f"<b>{settings.BOT_DISPLAY_NAME}</b>\n"
        f"🎯 <b>CAMPAIGN RUNNING</b> 🎯\n\n"
        f"⚡ <b>Rate:</b> {rate}/min per account\n"
        f"👥 <b>Accounts:</b> {account_count}\n"
        f"🆔 <b>Campaign ID:</b> {cid}\n\n"
        f"📌 <b>Targets Mode:</b> {mode.upper()}\n"
        f"🔖 <b>Types:</b> {types_line}\n"
        f"📇 <b>Include:</b> {include_count if mode == 'include' else 'All Dialogs'}\n"
        f"🚫 <b>Exclude:</b> {exclude_count} IDs\n"
        f"🧾 <b>Sample IDs:</b> {sample_ids}\n\n"
        f"📝 <b>Message:</b> {msg_preview}\n"
        f"🖼️ <b>Media:</b> {media_line} | 🔘 <b>Buttons:</b> {buttons_count}\n\n"
        f"Use 'Stop Campaign' to stop or 'Analytics' to monitor."
    )
    
    try:
        if cb.message.photo:
            await cb.message.edit_caption(caption=updated_text, parse_mode=ParseMode.HTML, reply_markup=main_menu_kb(campaign_running=True))
        else:
            await cb.message.edit_text(updated_text, parse_mode=ParseMode.HTML, reply_markup=main_menu_kb(campaign_running=True))
    except TelegramBadRequest as e:
        if "message is not modified" not in str(e):
            raise e


async def menu_stop(cb: CallbackQuery):
    """Stop running campaign"""
    db = get_db_sync()
    user = await db.users.find_one({"user_id": cb.from_user.id})
    active_id = (user or {}).get("active_campaign_id")
    
    if not active_id:
        await safe_answer_callback(cb, "❌ No campaign is currently running", show_alert=True)
        return
    
    try:
        oid = ObjectId(active_id)
    except Exception:
        oid = active_id
    
    # Stop the campaign
    await db.campaigns.update_one({"_id": oid}, {"$set": {"status": "stopped", "stopped_at": datetime.now(timezone.utc)}})
    await db.users.update_one({"user_id": cb.from_user.id}, {"$unset": {"active_campaign_id": 1}})
    
    # Show success message and update menu back to normal
    await safe_answer_callback(cb, "⏹️ Campaign Stopped Successfully!", show_alert=True)
    
    # Update the main menu back to normal state
    try:
        if cb.message.photo:
            await cb.message.edit_caption(caption=hero_caption(), parse_mode=ParseMode.HTML, reply_markup=main_menu_kb(campaign_running=False))
        else:
            await cb.message.edit_text(hero_caption(), parse_mode=ParseMode.HTML, reply_markup=main_menu_kb(campaign_running=False))
    except TelegramBadRequest as e:
        if "message is not modified" not in str(e):
            raise e


async def menu_analytics(cb: CallbackQuery):
    db = get_db_sync()
    owner = cb.from_user.id
    user = await db.users.find_one({"user_id": owner})
    cid = (user or {}).get("active_campaign_id")
    logs = db.logs
    match = {"owner_user_id": owner}
    if cid:
        match["campaign_id"] = cid
    sent = await logs.count_documents({**match, "event": {"$in": ["sent", "sent_after_fw"]}})
    attempts = await logs.count_documents({**match, "event": "attempt"})
    failed = await logs.count_documents({**match, "event": "failed"})
    fw = await logs.count_documents({**match, "event": "floodwait"})
    skipped = await logs.count_documents({**match, "event": "skipped"})
    reached_ids = await logs.distinct("chat_id", {**match, "event": {"$in": ["sent", "sent_after_fw"]}})
    reached = len(reached_ids)
    fail_top = []
    async for row in logs.aggregate([
        {"$match": {**match, "event": "failed"}},
        {"$group": {"_id": {"$ifNull": ["$fail_reason", "unknown"]}, "n": {"$sum": 1}}},
        {"$sort": {"n": -1}},
        {"$limit": 5},
    ]):
        fail_top.append((row.get("_id") or "unknown", row.get("n", 0)))
    skip_top = []
    async for row in logs.aggregate([
        {"$match": {**match, "event": "skipped"}},
        {"$group": {"_id": {"$ifNull": ["$reason", "unknown"]}, "n": {"$sum": 1}}},
        {"$sort": {"n": -1}},
        {"$limit": 5},
    ]):
        skip_top.append((row.get("_id") or "unknown", row.get("n", 0)))
    fails = "\n".join([f"• {k}: {v}" for k, v in fail_top]) or "• none"
    skips = "\n".join([f"• {k}: {v}" for k, v in skip_top]) or "• none"
    header = f"<b>📊 Analytics</b>\n"
    if cid:
        header += f"ID: <code>{cid}</code>\n\n"
    text = (
        header +
        f"🛰️ Attempts: <b>{attempts}</b>\n"
        f"✅ Sent: <b>{sent}</b>   ⏭️ Skipped: <b>{skipped}</b>   ❌ Failed: <b>{failed}</b>\n"
        f"🚦 FloodWaits: <b>{fw}</b>\n"
        f"📬 Unique Chats Reached: <b>{reached}</b>\n\n"
        f"<b>Top Failed Reasons</b>\n{fails}\n\n"
        f"<b>Top Skipped Reasons</b>\n{skips}"
    )
    try:
        if getattr(cb.message, "photo", None):
            try:
                await cb.message.edit_caption(caption=text, parse_mode=ParseMode.HTML, reply_markup=analytics_kb())
            except TelegramBadRequest as e:
                se = str(e)
                if "there is no caption" in se or "message is not modified" in se:
                    try:
                        await cb.message.edit_text(text, parse_mode=ParseMode.HTML, reply_markup=analytics_kb())
                    except TelegramBadRequest as e2:
                        if "message is not modified" not in str(e2):
                            raise
                else:
                    raise
        else:
            try:
                await cb.message.edit_text(text, parse_mode=ParseMode.HTML, reply_markup=analytics_kb())
            except TelegramBadRequest as e:
                if "message is not modified" not in str(e):
                    raise
    finally:
        await safe_answer_callback(cb)


async def analytics_refresh(cb: CallbackQuery):
    await menu_analytics(cb)


async def analytics_targets(cb: CallbackQuery):
    db = get_db_sync()
    owner = cb.from_user.id
    user = await db.users.find_one({"user_id": owner})
    cfg = (user or {}).get("config", {})
    targets_cfg = cfg.get("targets", {})
    _defaults_types = {"private": True, "group": True, "supergroup": True, "channel": True}
    _raw_types = targets_cfg.get("types") or {}
    types_cfg = {**_defaults_types, **{k: _to_bool(v) for k, v in _raw_types.items()}}
    mode = targets_cfg.get("mode", "include")
    include = targets_cfg.get("include", [])
    exclude = targets_cfg.get("exclude", [])
    cid = (user or {}).get("active_campaign_id")
    logs = db.logs
    match = {"owner_user_id": owner}
    if cid:
        match["campaign_id"] = cid
    type_counts = {}
    async for row in logs.aggregate([
        {"$match": {**match, "event": {"$in": ["attempt", "sent", "sent_after_fw"]}}},
        {"$group": {"_id": {"$ifNull": ["$chat_type", "unknown"]}, "n": {"$sum": 1}}},
        {"$sort": {"n": -1}}
    ]):
        type_counts[row.get("_id") or "unknown"] = row.get("n", 0)
    types_line = (
        f"Personal: {'ON' if types_cfg.get('private') else 'OFF'} | "
        f"Groups: {'ON' if types_cfg.get('group') else 'OFF'} | "
        f"Supergroups: {'ON' if types_cfg.get('supergroup') else 'OFF'} | "
        f"Channels: {'ON' if types_cfg.get('channel') else 'OFF'}"
    )
    attempts_sample = []
    async for ev in logs.find({**match, "event": {"$in": ["attempt", "sent", "sent_after_fw"]}}).sort("ts", -1).limit(10):
        t = ev.get("chat_type", "?")
        title = ev.get("chat_title", "?")
        attempts_sample.append(f"• {t} | {title} | <code>{ev.get('chat_id')}</code>")
    sample_block = "\n".join(attempts_sample) or "• none yet"
    text = (
        f"<b>🎯 Targets</b>\n"
        f"Mode: <b>{mode.upper()}</b>\n"
        f"Types: {types_line}\n"
        f"Include: <b>{len(include) if mode=='include' else 'All Dialogs'}</b> | Exclude: <b>{len(exclude)}</b>\n\n"
        f"<b>By Type (from activity)</b>\n" +
        ("\n".join([f"• {k}: {v}" for k, v in type_counts.items()]) if type_counts else "• none yet") +
        f"\n\n<b>Recent Targets</b>\n{sample_block}"
    )
    try:
        await cb.message.edit_caption(caption=text, parse_mode=ParseMode.HTML, reply_markup=analytics_kb())
    except Exception:
        await cb.message.edit_text(text, parse_mode=ParseMode.HTML, reply_markup=analytics_kb())
    await safe_answer_callback(cb)


async def menu_autoreply(cb: CallbackQuery):
    text = (
        "<b>Auto Reply</b>\n"
        "Define rules, rate limits, and scope.\n\n"
        "[Placeholder – configuration UI]"
    )
    if cb.message.photo:
        await cb.message.edit_caption(caption=text, parse_mode=ParseMode.HTML, reply_markup=back_to_menu_kb())
    else:
        await cb.message.edit_text(text, parse_mode=ParseMode.HTML, reply_markup=back_to_menu_kb())
    await safe_answer_callback(cb)


async def menu_policy(cb: CallbackQuery):
    text = f"<b>Policy</b>\n{settings.POLICY_TEXT}"
    if cb.message.photo:
        await cb.message.edit_caption(caption=text, parse_mode=ParseMode.HTML, reply_markup=back_to_menu_kb())
    else:
        await cb.message.edit_text(text, parse_mode=ParseMode.HTML, reply_markup=back_to_menu_kb())
    await safe_answer_callback(cb)


async def menu_login(cb: CallbackQuery):
    """Handle login menu"""
    user_id = cb.from_user.id
    account_count = await session_manager.get_account_count(user_id)
    
    text = (
        "<b>🔑 Account Login</b>\n\n"
        f"📱 <b>Current Accounts:</b> {account_count}/{settings.MAX_ACCOUNTS_PER_USER}\n\n"
        "🎯 <b>Login Benefits:</b>\n"
        "• Access private channels\n"
        "• Send messages from your accounts\n"
        "• Bypass rate limits with multiple accounts\n\n"
        "🔒 <b>Security:</b> Sessions are encrypted and stored securely"
    )
    
    try:
        if cb.message.photo:
            await cb.message.edit_caption(caption=text, parse_mode=ParseMode.HTML, reply_markup=login_menu_kb())
        else:
            await cb.message.edit_text(text, parse_mode=ParseMode.HTML, reply_markup=login_menu_kb())
    except TelegramBadRequest as e:
        if "message is not modified" in str(e):
            # Message content is identical, just answer the callback
            pass
        else:
            # Re-raise other telegram errors
            raise e
    await safe_answer_callback(cb)


async def menu_accounts(cb: CallbackQuery):
    """Handle accounts menu"""
    user_id = cb.from_user.id
    accounts = await session_manager.get_user_sessions(user_id)
    
    if not accounts:
        text = (
            "<b>👥 My Accounts</b>\n\n"
            "❌ <i>No accounts logged in</i>\n\n"
            "🔑 <b>Get Started:</b>\n"
            "• Tap 'Add Account' to login\n"
            "• Follow the setup instructions\n"
            "• Start sending campaigns\n\n"
            "💡 <i>You can add up to 3 accounts</i>"
        )
    else:
        account_list = []
        for acc in accounts:
            status_emoji = "✅" if acc["status"] == "active" else "❌"
            last_used = acc["last_used"].strftime("%m/%d %H:%M")
            account_list.append(f"• {status_emoji} {acc['phone']} (Last: {last_used})")
        
        text = (
            "<b>👥 My Accounts</b>\n\n"
            f"📱 <b>Total:</b> {len(accounts)}/{settings.MAX_ACCOUNTS_PER_USER}\n\n"
            "<b>Accounts:</b>\n" + "\n".join(account_list) + "\n\n"
            "💡 <i>Tap an account to manage it</i>"
        )
    
    try:
        if cb.message.photo:
            await cb.message.edit_caption(caption=text, parse_mode=ParseMode.HTML, reply_markup=accounts_menu_kb(accounts))
        else:
            await cb.message.edit_text(text, parse_mode=ParseMode.HTML, reply_markup=accounts_menu_kb(accounts))
    except TelegramBadRequest as e:
        if "message is not modified" in str(e):
            # Message content is identical, just answer the callback
            pass
        else:
            # Re-raise other telegram errors
            raise e
    
    await safe_answer_callback(cb)


async def login_start(cb: CallbackQuery):
    """Start login process"""
    user_id = cb.from_user.id
    
    # Check if user can add more accounts
    if not await session_manager.can_add_account(user_id):
        await safe_answer_callback(cb, f"❌ Maximum {settings.MAX_ACCOUNTS_PER_USER} accounts allowed", show_alert=True)
        return
    
    # Set user state for phone input
    db = get_db_sync()
    await db.users.update_one(
        {"user_id": user_id}, 
        {"$set": {"state": "await_phone_number"}}, 
        upsert=True
    )
    
    text = (
        "<b>🔑 Login Setup</b>\n\n"
        "📱 <b>Step 1:</b> <i>Enter your phone number</i>\n\n"
        "📝 <b>Format:</b> Include country code\n"
        "• Example: <code>+19876543210</code>\n"
        "• Example: <code>+911234567890</code>\n\n"
        "⚠️ <i>Make sure the number is correct</i>\n\n"
        "💬 <i>Send your phone number now...</i>"
    )
    
    if cb.message.photo:
        await cb.message.edit_caption(caption=text, parse_mode=ParseMode.HTML, reply_markup=back_to_menu_kb())
    else:
        await cb.message.edit_text(text, parse_mode=ParseMode.HTML, reply_markup=back_to_menu_kb())
    await safe_answer_callback(cb)


async def login_help(cb: CallbackQuery):
    """Show login help"""
    text = (
        "<b>❓ How to Login</b>\n\n"
        "<b>📋 Step-by-Step:</b>\n"
        "1️⃣ Tap 'Start Login'\n"
        "2️⃣ Enter phone number with country code\n"
        "3️⃣ Enter OTP code <b>with spaces</b> (1 2 3 4 5)\n"
        "4️⃣ Enter 2FA password if required\n\n"
        "<b>⚠️ Important:</b>\n"
        "• Use spaces in OTP to avoid Telegram expiry\n"
        "• Keep your Telegram app ready\n"
        "• Complete within time limits\n\n"
        "<b>🔒 Security:</b>\n"
        "• Sessions are encrypted\n"
        "• Stored securely in database\n"
        "• Survive bot restarts"
    )
    
    if cb.message.photo:
        await cb.message.edit_caption(caption=text, parse_mode=ParseMode.HTML, reply_markup=back_to_menu_kb())
    else:
        await cb.message.edit_text(text, parse_mode=ParseMode.HTML, reply_markup=back_to_menu_kb())
    await safe_answer_callback(cb)


async def account_view(cb: CallbackQuery):
    """View account details"""
    account_id = cb.data.split(":")[-1]
    user_id = cb.from_user.id
    
    # Get account details from database
    db = get_db_sync()
    account = await db.accounts.find_one({"_id": ObjectId(account_id), "owner_user_id": user_id})
    
    if not account:
        await safe_answer_callback(cb, "❌ Account not found", show_alert=True)
        return
    
    created = account["created_at"].strftime("%Y-%m-%d %H:%M")
    last_used = account.get("last_used", account["created_at"]).strftime("%Y-%m-%d %H:%M")
    status = account.get("status", "active")
    
    text = (
        f"<b>📱 Account Details</b>\n\n"
        f"📞 <b>Phone:</b> {account['phone']}\n"
        f"📝 <b>Name:</b> {account.get('account_name', 'N/A')}\n"
        f"📅 <b>Added:</b> {created}\n"
        f"🕒 <b>Last Used:</b> {last_used}\n"
        f"📊 <b>Status:</b> {status.title()}\n\n"
        "🔧 <b>Actions:</b>\n"
        "• Test connection to verify account\n"
        "• Logout to deactivate session\n"
        "• Delete to remove completely"
    )
    
    if cb.message.photo:
        await cb.message.edit_caption(caption=text, parse_mode=ParseMode.HTML, reply_markup=account_detail_kb(account_id))
    else:
        await cb.message.edit_text(text, parse_mode=ParseMode.HTML, reply_markup=account_detail_kb(account_id))
    await safe_answer_callback(cb)


async def account_logout(cb: CallbackQuery):
    """Logout account"""
    account_id = cb.data.split(":")[-1]
    user_id = cb.from_user.id
    
    # Get account details
    db = get_db_sync()
    account = await db.accounts.find_one({"_id": ObjectId(account_id), "owner_user_id": user_id})
    
    if not account:
        await safe_answer_callback(cb, "❌ Account not found", show_alert=True)
        return
    
    # Deactivate session
    success = await session_manager.deactivate_session(user_id, account["phone"])
    
    if success:
        await safe_answer_callback(cb, "✅ Account logged out successfully")
        # Redirect to accounts menu
        await menu_accounts(cb)
    else:
        await safe_answer_callback(cb, "❌ Failed to logout account", show_alert=True)


async def account_delete(cb: CallbackQuery):
    """Delete account"""
    account_id = cb.data.split(":")[-1]
    user_id = cb.from_user.id
    
    # Get account details
    db = get_db_sync()
    account = await db.accounts.find_one({"_id": ObjectId(account_id), "owner_user_id": user_id})
    
    if not account:
        await safe_answer_callback(cb, "❌ Account not found", show_alert=True)
        return
    
    # Delete session
    success = await session_manager.delete_session(user_id, account["phone"])
    
    if success:
        await safe_answer_callback(cb, "✅ Account deleted successfully")
        # Redirect to accounts menu
        await menu_accounts(cb)
    else:
        await safe_answer_callback(cb, "❌ Failed to delete account", show_alert=True)


async def account_test(cb: CallbackQuery):
    """Test account connection"""
    account_id = cb.data.split(":")[-1]
    user_id = cb.from_user.id
    
    # Get account details
    db = get_db_sync()
    account = await db.accounts.find_one({"_id": ObjectId(account_id), "owner_user_id": user_id})
    
    if not account:
        await safe_answer_callback(cb, "❌ Account not found", show_alert=True)
        return
    
    await safe_answer_callback(cb, "🔄 Testing connection...", show_alert=False)
    
    # Test connection
    result = await telegram_login_manager.test_account_connection(user_id, account["phone"])
    
    if result["success"]:
        account_info = result["account_info"]
        text = (
            f"<b>✅ Connection Test Successful</b>\n\n"
            f"📱 <b>Phone:</b> {account['phone']}\n"
            f"👤 <b>Name:</b> {account_info.get('first_name', 'N/A')} {account_info.get('last_name', '')}\n"
            f"🆔 <b>User ID:</b> {account_info.get('id', 'N/A')}\n"
            f"🏷️ <b>Username:</b> @{account_info.get('username', 'N/A')}\n\n"
            "🎯 <i>Account is working properly</i>"
        )
        
        if cb.message.photo:
            await cb.message.edit_caption(caption=text, parse_mode=ParseMode.HTML, reply_markup=account_detail_kb(account_id))
        else:
            await cb.message.edit_text(text, parse_mode=ParseMode.HTML, reply_markup=account_detail_kb(account_id))
    else:
        error_msg = result["message"]
        text = (
            f"<b>❌ Connection Test Failed</b>\n\n"
            f"📱 <b>Phone:</b> {account['phone']}\n"
            f"❌ <b>Error:</b> {error_msg}\n\n"
            "🔧 <b>Possible Solutions:</b>\n"
            "• Account may be logged out\n"
            "• Session may have expired\n"
            "• Try logging in again"
        )
        
        if cb.message.photo:
            await cb.message.edit_caption(caption=text, parse_mode=ParseMode.HTML, reply_markup=account_detail_kb(account_id))
        else:
            await cb.message.edit_text(text, parse_mode=ParseMode.HTML, reply_markup=account_detail_kb(account_id))


def _extract_buttons_from_reply_markup(reply_markup) -> list:
    """Extract buttons from reply markup for storage"""
    if not reply_markup or not hasattr(reply_markup, 'inline_keyboard'):
        return []
    
    buttons = []
    for row in reply_markup.inline_keyboard:
        button_row = []
        for button in row:
            button_data = {
                "text": button.text,
            }
            if button.url:
                button_data["url"] = button.url
            elif button.callback_data:
                button_data["callback_data"] = button.callback_data
            button_row.append(button_data)
        if button_row:
            buttons.append(button_row)
    
    return buttons


async def content_message_handler(message: Message):
    db = get_db_sync()
    user = await db.users.find_one({"user_id": message.from_user.id})
    state = (user or {}).get("state")
    if state == "await_ad_message":
        media_info = None
        text = message.html_text or (message.caption or "") or (message.text or "")
        # Prepare media storage dir
        base_dir = os.path.join(os.getcwd(), "data", "media", str(message.from_user.id))
        os.makedirs(base_dir, exist_ok=True)
        if message.photo:
            f = message.photo[-1]
            file_path = os.path.join(base_dir, f"photo_{message.message_id}.jpg")
            try:
                await message.bot.download(f, destination=file_path)
                media_info = {"type": "photo", "path": file_path}
            except Exception:
                media_info = None
        elif message.document:
            f = message.document
            file_path = os.path.join(base_dir, f"doc_{message.message_id}_{f.file_name or 'file'}")
            try:
                await message.bot.download(f, destination=file_path)
                media_info = {"type": "document", "path": file_path, "mime": f.mime_type}
            except Exception:
                media_info = None
        elif message.video:
            f = message.video
            file_path = os.path.join(base_dir, f"video_{message.message_id}.mp4")
            try:
                await message.bot.download(f, destination=file_path)
                media_info = {"type": "video", "path": file_path}
            except Exception:
                media_info = None

        payload = {
            "text": text,
            "media": media_info,
            "buttons": _extract_buttons_from_reply_markup(message.reply_markup) if message.reply_markup else [],
            "message_id": message.message_id,  # Store original message ID for forwarding
            "chat_id": message.chat.id,       # Store chat ID for forwarding
            "from_user_id": message.from_user.id  # Store user ID
        }
        await db.users.update_one({"user_id": message.from_user.id}, {"$set": {"config.message": payload}, "$unset": {"state": 1}}, upsert=True)
        
        # Reply with success message but DON'T delete the original
        success_msg = await message.reply(
            "<b>✅ Message Saved Successfully!</b>\n\n"
            "🎯 <i>Your message is ready for campaigns</i>\n"
            "📝 <i>Original message preserved for forwarding</i>\n\n"
            "💡 <i>You can now start your ads campaign</i>",
            parse_mode=ParseMode.HTML
        )
        
        # Delete only the success message after 3 seconds
        await asyncio.sleep(3)
        try:
            await success_msg.delete()
        except Exception:
            pass
    elif state == "await_include_ids":
        try:
            ids = [int(x.strip()) for x in message.text.split(',') if x.strip()]
        except Exception:
            await message.reply("Invalid list. Provide comma-separated numeric IDs.")
            return
        await db.users.update_one({"user_id": message.from_user.id}, {"$set": {"config.targets.mode": "include", "config.targets.include": ids}, "$unset": {"state": 1}}, upsert=True)
        success_msg = await message.reply("✅ Include IDs saved successfully!")
        
        # Delete both messages after 2 seconds
        await asyncio.sleep(2)
        try:
            await message.delete()
            await success_msg.delete()
        except Exception:
            pass
    elif state == "await_exclude_ids":
        try:
            ids = [int(x.strip()) for x in message.text.split(',') if x.strip()]
        except Exception:
            await message.reply("Invalid list. Provide comma-separated numeric IDs.")
            return
        await db.users.update_one({"user_id": message.from_user.id}, {"$set": {"config.targets.exclude": ids}, "$unset": {"state": 1}}, upsert=True)
        success_msg = await message.reply("✅ Exclude IDs saved successfully!")
        
        # Delete both messages after 2 seconds
        await asyncio.sleep(2)
        try:
            await message.delete()
            await success_msg.delete()
        except Exception:
            pass
    elif state == "await_custom_rate":
        try:
            val = int(message.text.strip())
            if val <= 0:
                raise ValueError
        except Exception:
            await message.reply("Send a positive integer.")
            return
        await db.users.update_one({"user_id": message.from_user.id}, {"$set": {"config.rate_per_min": val}, "$unset": {"state": 1}}, upsert=True)
        success_msg = await message.reply(f"✅ Custom interval set to {val}/min successfully!")
        
        # Delete both messages after 2 seconds
        await asyncio.sleep(2)
        try:
            await message.delete()
            await success_msg.delete()
        except Exception:
            pass
    elif state == "await_phone_number":
        phone_number = message.text.strip()
        # Basic phone validation
        if not phone_number.startswith('+') or len(phone_number) < 10:
            await message.reply(
                "❌ Invalid phone format. Please include country code (e.g., +1234567890)"
            )
            return
        
        # Send OTP using real Telegram login
        result = await telegram_login_manager.start_login_process(message.from_user.id, phone_number)
        
        if result["success"]:
            # Store phone and move to OTP state
            await db.users.update_one(
                {"user_id": message.from_user.id}, 
                {"$set": {"state": "await_otp", "temp_phone": phone_number}}, 
                upsert=True
            )
            
            await message.reply(
                "<b>📲 OTP Verification</b>\n\n"
                "📬 <b>Step 2:</b> <i>Check your Telegram app</i>\n\n"
                "🔢 <b>Enter OTP with spaces:</b>\n"
                "• Received: <code>12345</code>\n"
                "• Enter as: <code>1 2 3 4 5</code>\n\n"
                "⏰ <i>You have 10 minutes to complete this step</i>",
                parse_mode=ParseMode.HTML
            )
        else:
            error_msg = result["message"]
            if result.get("error") == "flood_wait":
                pass
            elif result.get("error") == "invalid_phone":
                await message.reply(
                    "<b>❌ Invalid Phone Number</b>\n\n"
                    "📱 <i>The number you entered is not valid</i>\n\n"
                    "🔄 <b>Please check:</b>\n"
                    "• Include country code (+1, +91, etc.)\n"
                    "• No spaces or special characters\n\n"
                    "🔄 <i>Try the login process again</i>",
                    parse_mode=ParseMode.HTML
                )
            else:
                await message.reply(f"❌ Error: {error_msg}")
        
        try:
            await message.delete()
        except Exception:
            pass
            
    elif state == "await_otp":
        otp_code = message.text.strip()
        user_data = await db.users.find_one({"user_id": message.from_user.id})
        phone_number = user_data.get("temp_phone")
        
        if not phone_number:
            await message.reply("❌ Session expired. Please start login again.")
            return
        
        # Verify OTP using real Telegram login
        result = await telegram_login_manager.verify_otp(message.from_user.id, otp_code)
        
        if result["success"]:
            # Clear state
            await db.users.update_one(
                {"user_id": message.from_user.id}, 
                {"$unset": {"state": 1, "temp_phone": 1}}
            )
            
            await message.reply(
                "<b>✅ Login Successful!</b>\n\n"
                f"📱 <b>Account:</b> {result['phone']}\n"
                f"🆔 <b>ID:</b> {result['account_id'][:8]}...\n\n"
                "🎯 <i>Account is now ready for campaigns</i>\n\n"
                "💡 <i>Use 'My Accounts' to manage your sessions</i>",
                parse_mode=ParseMode.HTML
            )
        elif result.get("needs_password"):
            # 2FA required
            await db.users.update_one(
                {"user_id": message.from_user.id}, 
                {"$set": {"state": "await_2fa_password"}}, 
                upsert=True
            )
            
            await message.reply(
                "<b>🔐 Two-Step Verification</b>\n\n"
                "🛡️ <i>Your account has 2FA enabled</i>\n\n"
                "🔑 <b>Step 3:</b> <i>Enter your password</i>\n\n"
                "⏰ <i>You have 5 minutes to complete this step</i>",
                parse_mode=ParseMode.HTML
            )
        else:
            error_msg = result["message"]
            if result.get("error") == "invalid_otp":
                await message.reply(
                    "<b>❌ Invalid OTP</b>\n\n"
                    "🔢 <i>The verification code is incorrect</i>\n\n"
                    "🔄 <b>Please check:</b>\n"
                    "• Enter code with spaces (1 2 3 4 5)\n"
                    "• Make sure all digits are correct\n\n"
                    "🔄 <i>Try entering the OTP again</i>",
                    parse_mode=ParseMode.HTML
                )
            elif result.get("error") == "expired_otp":
                # Clear state since session expired
                await db.users.update_one(
                    {"user_id": message.from_user.id}, 
                    {"$unset": {"state": 1, "temp_phone": 1}}
                )
                await message.reply(
                    "<b>❌ Expired OTP</b>\n\n"
                    "⏰ <i>The verification code has expired</i>\n\n"
                    "🔄 <b>Next Steps:</b>\n"
                    "• Start login process again\n"
                    "• Enter the new code faster\n\n"
                    "⚡ <i>Tip: OTP codes expire after 10 minutes</i>",
                    parse_mode=ParseMode.HTML
                )
            else:
                await message.reply(f"❌ Error: {error_msg}")
        
        try:
            await message.delete()
        except Exception:
            pass
            
    elif state == "await_2fa_password":
        password = message.text.strip()
        
        # Verify 2FA password
        result = await telegram_login_manager.verify_2fa_password(message.from_user.id, password)
        
        if result["success"]:
            # Clear state
            await db.users.update_one(
                {"user_id": message.from_user.id}, 
                {"$unset": {"state": 1, "temp_phone": 1}}
            )
            
            await message.reply(
                "<b>✅ Login Successful!</b>\n\n"
                f"📱 <b>Account:</b> {result['phone']}\n"
                f"🆔 <b>ID:</b> {result['account_id'][:8]}...\n\n"
                "🔐 <i>2FA verification completed</i>\n\n"
                "🎯 <i>Account is now ready for campaigns</i>",
                parse_mode=ParseMode.HTML
            )
        else:
            error_msg = result["message"]
            if result.get("error") == "invalid_password":
                await message.reply(
                    "<b>❌ Invalid Password</b>\n\n"
                    "🔐 <i>The 2FA password is incorrect</i>\n\n"
                    "🔄 <b>Please check:</b>\n"
                    "• Make sure it's your Telegram password\n"
                    "• Check for typos or case sensitivity\n\n"
                    "🔄 <i>Try entering the password again</i>",
                    parse_mode=ParseMode.HTML
                )
            else:
                await message.reply(f"❌ Error: {error_msg}")
        
        try:
            await message.delete()
        except Exception:
            pass


async def main():
    logging.basicConfig(level=logging.INFO)
    bot = Bot(settings.BOT_TOKEN, default=DefaultBotProperties(parse_mode=ParseMode.HTML))
    dp = Dispatcher()

    dp.startup.register(on_startup)

    dp.message.register(start_handler, CommandStart())
    # Admin commands
    dp.message.register(admin_setmax, Command("setmax"))
    dp.message.register(admin_setpresets, Command("setpresets"))
    dp.message.register(admin_block, Command("block"))
    dp.message.register(admin_unblock, Command("unblock"))
    dp.message.register(admin_showconfig, Command("config"))
    dp.message.register(admin_cleanup, Command("cleanup"))
    dp.message.register(admin_accounts, Command("accounts"))
    dp.message.register(admin_testdialogs, Command("testdialogs"))
    dp.message.register(admin_testsend, Command("testsend"))
    dp.message.register(admin_campaign, Command("campaign"))
    # Content/config handlers
    dp.message.register(content_message_handler, F.text)
    dp.callback_query.register(menu_home, F.data == "menu:home")
    
    # Login and accounts handlers
    dp.callback_query.register(menu_login, F.data == "menu:login")
    dp.callback_query.register(menu_accounts, F.data == "menu:accounts")
    dp.callback_query.register(login_start, F.data == "login:start")
    dp.callback_query.register(login_help, F.data == "login:help")
    dp.callback_query.register(account_view, F.data.startswith("account:view:"))
    dp.callback_query.register(account_test, F.data.startswith("account:test:"))
    dp.callback_query.register(account_logout, F.data.startswith("account:logout:"))
    dp.callback_query.register(account_delete, F.data.startswith("account:delete:"))
    
    # Original handlers
    dp.callback_query.register(menu_set_msg, F.data == "menu:set_msg")
    dp.callback_query.register(menu_view_msg, F.data == "menu:view_msg")
    dp.callback_query.register(menu_targets, F.data == "menu:targets")
    dp.callback_query.register(targets_include, F.data == "targets:include")
    dp.callback_query.register(targets_all, F.data == "targets:all")
    dp.callback_query.register(targets_exclude, F.data == "targets:exclude")
    dp.callback_query.register(targets_type_toggle, F.data.startswith("targets:type:"))
    dp.callback_query.register(menu_interval, F.data == "menu:interval")
    dp.callback_query.register(interval_preset, F.data.in_({"interval:safe", "interval:default", "interval:aggressive"}))
    dp.callback_query.register(interval_custom, F.data == "interval:custom")
    dp.callback_query.register(menu_start, F.data == "menu:start")
    dp.callback_query.register(menu_stop, F.data == "menu:stop")
    dp.callback_query.register(menu_analytics, F.data == "menu:analytics")
    dp.callback_query.register(analytics_refresh, F.data == "analytics:refresh")
    dp.callback_query.register(analytics_targets, F.data == "analytics:targets")
    dp.callback_query.register(menu_autoreply, F.data == "menu:autoreply")
    dp.callback_query.register(menu_policy, F.data == "menu:policy")

    await dp.start_polling(bot, allowed_updates=dp.resolve_used_update_types())


# ===== Admin Commands =====

def _is_admin(uid: int) -> bool:
    return uid in settings.ADMIN_IDS


async def admin_setmax(message: Message):
    if not _is_admin(message.from_user.id):
        return
    parts = (message.text or "").split()
    if len(parts) != 2 or not parts[1].isdigit():
        await message.reply("Usage: /setmax <number>")
        return
    val = int(parts[1])
    db = get_db_sync()
    await db.config.update_one({"_id": "runtime"}, {"$set": {"MAX_ACCOUNTS_PER_USER": val}}, upsert=True)
    await message.reply(f"MAX_ACCOUNTS_PER_USER set to {val}")


async def admin_setpresets(message: Message):
    if not _is_admin(message.from_user.id):
        return
    # Usage: /setpresets safe=<n> default=<n> aggressive=<n>
    text = (message.text or "")[len("/setpresets"):].strip()
    kv = dict((p.split("=")[0].strip(), int(p.split("=")[1])) for p in text.split() if "=" in p)
    db = get_db_sync()
    updates = {}
    if "safe" in kv:
        updates["INTERVAL_PRESETS_SAFE"] = kv["safe"]
    if "default" in kv:
        updates["INTERVAL_PRESETS_DEFAULT"] = kv["default"]
    if "aggressive" in kv:
        updates["INTERVAL_PRESETS_AGGRESSIVE"] = kv["aggressive"]
    if not updates:
        await message.reply("Usage: /setpresets safe=<n> default=<n> aggressive=<n>")
        return
    await db.config.update_one({"_id": "runtime"}, {"$set": updates}, upsert=True)
    await message.reply(f"Presets updated: {updates}")


async def admin_block(message: Message):
    if not _is_admin(message.from_user.id):
        return
    parts = (message.text or "").split()
    if len(parts) != 2 or not parts[1].isdigit():
        await message.reply("Usage: /block <user_id>")
        return
    uid = int(parts[1])
    db = get_db_sync()
    await db.users.update_one({"user_id": uid}, {"$set": {"blocked": True}}, upsert=True)
    await message.reply(f"User {uid} blocked")


async def admin_unblock(message: Message):
    if not _is_admin(message.from_user.id):
        return
    parts = (message.text or "").split()
    if len(parts) != 2 or not parts[1].isdigit():
        await message.reply("Usage: /unblock <user_id>")
        return
    uid = int(parts[1])
    db = get_db_sync()
    await db.users.update_one({"user_id": uid}, {"$set": {"blocked": False}}, upsert=True)
    await message.reply(f"User {uid} unblocked")


async def admin_showconfig(message: Message):
    if not _is_admin(message.from_user.id):
        return
    db = get_db_sync()
    cfg = await db.config.find_one({"_id": "runtime"}) or {}
    await message.reply(f"Runtime config: {cfg}")


async def admin_cleanup(message: Message):
    """Clean up stale campaign IDs for debugging"""
    if not _is_admin(message.from_user.id):
        return
    
    db = get_db_sync()
    
    # Find all users with active campaign IDs
    users_with_campaigns = db.users.find({"active_campaign_id": {"$exists": True}})
    cleaned = 0
    
    async for user in users_with_campaigns:
        active_id = user.get("active_campaign_id")
        if active_id:
            try:
                oid = ObjectId(active_id)
                campaign = await db.campaigns.find_one({"_id": oid})
                if not campaign or campaign.get("status") != "running":
                    # Clean up stale campaign ID
                    await db.users.update_one({"user_id": user["user_id"]}, {"$unset": {"active_campaign_id": 1}})
                    cleaned += 1
            except Exception:
                # Invalid campaign ID, clean it up
                await db.users.update_one({"user_id": user["user_id"]}, {"$unset": {"active_campaign_id": 1}})
                cleaned += 1
    
    await message.reply(f"Cleaned up {cleaned} stale campaign references")


async def admin_accounts(message: Message):
    """Debug: Show account info for a user"""
    if not _is_admin(message.from_user.id):
        return
    
    parts = message.text.split()
    if len(parts) != 2:
        await message.reply("Usage: /accounts <user_id>")
        return
    
    try:
        user_id = int(parts[1])
    except ValueError:
        await message.reply("Invalid user ID")
        return
    
    db = get_db_sync()
    
    # Check accounts in database
    accounts = []
    async for acc in db.accounts.find({"owner_user_id": user_id}):
        accounts.append({
            "phone": acc.get("phone", "unknown"),
            "is_active": acc.get("is_active", False),
            "status": acc.get("status", "unknown"),
            "created": acc.get("created_at", "unknown"),
            "session_length": len(acc.get("session_string", ""))
        })
    
    if not accounts:
        await message.reply(f"No accounts found for user {user_id}")
        return
    
    text = f"**Accounts for user {user_id}:**\n\n"
    for i, acc in enumerate(accounts, 1):
        text += f"{i}. Phone: {acc['phone']}\n"
        text += f"   Active: {acc['is_active']}\n"
        text += f"   Status: {acc['status']}\n"
        text += f"   Session: {acc['session_length']} chars\n\n"
    
    await message.reply(text)


async def admin_testdialogs(message: Message):
    """Test: Check what dialogs the account can actually access"""
    if not _is_admin(message.from_user.id):
        return
    
    parts = message.text.split()
    if len(parts) != 2:
        await message.reply("Usage: /testdialogs <user_id>")
        return
    
    try:
        user_id = int(parts[1])
    except ValueError:
        await message.reply("Invalid user ID")
        return
    
    from pyrogram import Client
    from pyrogram.enums import ParseMode as PParseMode
    from app.core.security import decrypt
    
    db = get_db_sync()
    
    # Get first active account
    account = await db.accounts.find_one({"owner_user_id": user_id, "is_active": True})
    if not account:
        await message.reply(f"No active accounts found for user {user_id}")
        return
    
    try:
        session = decrypt(account["session_string"])
        client = Client(name=f":memory:{account['_id']}", api_id=settings.API_ID, api_hash=settings.API_HASH, session_string=session)
        await client.connect()
        
        dialogs = []
        count = 0
        async for dialog in client.get_dialogs():
            if count >= 5:  # Only check first 5
                break
            try:
                chat_info = await client.get_chat(dialog.chat.id)
                dialogs.append(f"{dialog.chat.id}: {chat_info.type} - {chat_info.title}")
                count += 1
            except Exception as e:
                dialogs.append(f"{dialog.chat.id}: ERROR - {e}")
                count += 1
        
        await client.disconnect()
        
        text = f"**Dialog Test for {account['phone']}:**\n\n"
        text += "\n".join(dialogs)
        
        await message.reply(text)
        
    except Exception as e:
        await message.reply(f"Test failed: {e}")


async def admin_testsend(message: Message):
    """CRITICAL TEST: Try to send a message to specific chat ID"""
    if not _is_admin(message.from_user.id):
        return
    
    parts = message.text.split()
    if len(parts) != 3:
        await message.reply("Usage: /testsend <user_id> <chat_id>")
        return
    
    try:
        user_id = int(parts[1])
        chat_id = int(parts[2])
    except ValueError:
        await message.reply("Invalid user_id or chat_id")
        return
    
    from pyrogram import Client
    from pyrogram.enums import ParseMode as PParseMode
    from app.core.security import decrypt
    
    db = get_db_sync()
    
    # Get first active account
    account = await db.accounts.find_one({"owner_user_id": user_id, "is_active": True})
    if not account:
        await message.reply(f"No active accounts found for user {user_id}")
        return
    
    try:
        session = decrypt(account["session_string"])
        client = Client(name=f":memory:{account['_id']}", api_id=settings.API_ID, api_hash=settings.API_HASH, session_string=session)
        await client.connect()
        
        # Test 1: Try to get chat info
        try:
            chat_info = await client.get_chat(chat_id)
            result = f"✅ Chat Access: {chat_info.type} - {chat_info.title}\n"
        except Exception as e:
            result = f"❌ Chat Access Failed: {e}\n"
            await client.disconnect()
            await message.reply(result)
            return
        
        # Test 2: Try different peer ID formats
        peer_formats = [chat_id]
        if str(chat_id).startswith('-100'):
            alt_id = int(str(chat_id)[4:])
            peer_formats.extend([alt_id, -alt_id])
        
        for peer_id in peer_formats:
            try:
                await client.send_message(peer_id, "🔧 TEST MESSAGE - Bot is working!", parse_mode=PParseMode.HTML)
                result += f"✅ Send SUCCESS with peer ID: {peer_id}\n"
                break
            except Exception as e:
                result += f"❌ Send FAILED with peer ID {peer_id}: {e}\n"
        
        await client.disconnect()
        await message.reply(result)
        
    except Exception as e:
        await message.reply(f"Test failed: {e}")


async def admin_campaign(message: Message):
    """Debug: Show campaign info for a user"""
    if not _is_admin(message.from_user.id):
        return
    
    parts = message.text.split()
    if len(parts) != 2:
        await message.reply("Usage: /campaign <user_id>")
        return
    
    try:
        user_id = int(parts[1])
    except ValueError:
        await message.reply("Invalid user ID")
        return
    
    db = get_db_sync()
    
    # Check user config
    user = await db.users.find_one({"user_id": user_id})
    if not user:
        await message.reply(f"User {user_id} not found")
        return
    
    cfg = user.get("config", {})
    active_id = user.get("active_campaign_id")
    
    text = f"**User {user_id} Config:**\n\n"
    text += f"**Message:** {'✅ Set' if cfg.get('message') else '❌ Not set'}\n"
    if cfg.get('message'):
        msg = cfg['message']
        text += f"  Text: {msg.get('text', 'None')[:50]}...\n"
        text += f"  Media: {'Yes' if msg.get('media') else 'No'}\n"
        text += f"  Buttons: {len(msg.get('buttons', []))}\n"
    
    targets = cfg.get("targets", {})
    text += f"\n**Targets:**\n"
    text += f"  Mode: {targets.get('mode', 'include')}\n"
    text += f"  Include: {len(targets.get('include', []))} IDs\n"
    if targets.get('include'):
        text += f"  Include IDs: {targets.get('include')[:5]}...\n"  # Show first 5 IDs
    text += f"  Exclude: {len(targets.get('exclude', []))} IDs\n"
    if targets.get('exclude'):
        text += f"  Exclude IDs: {targets.get('exclude')[:5]}...\n"  # Show first 5 IDs
    t = targets.get('types') or {}
    if t:
        text += f"  Types: private={t.get('private')} group={t.get('group')} supergroup={t.get('supergroup')} channel={t.get('channel')}\n"
    
    text += f"\n**Rate:** {cfg.get('rate_per_min', 'default')} msg/min\n"
    
    if active_id:
        campaign = await db.campaigns.find_one({"_id": ObjectId(active_id)})
        if campaign:
            text += f"\n**Active Campaign:** {active_id}\n"
            text += f"  Status: {campaign.get('status')}\n"
            text += f"  Created: {campaign.get('created_at')}\n"
            text += f"  Campaign Mode: {campaign.get('mode')}\n"
            text += f"  Campaign Targets: {len(campaign.get('targets', []))} IDs\n"
            if campaign.get('targets'):
                text += f"  First Target: {campaign.get('targets')[0]}\n"
        else:
            text += f"\n**Active Campaign ID:** {active_id} (NOT FOUND)\n"
    else:
        text += f"\n**Active Campaign:** None\n"
    
    await message.reply(text)


if __name__ == "__main__":
    asyncio.run(main())
