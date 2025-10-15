from __future__ import annotations

import asyncio
import logging
from arq import create_pool
from arq.connections import RedisSettings
from urllib.parse import urlparse
import ssl as _ssl
from datetime import datetime, timezone
from typing import List, Dict, Any, Set
from pyrogram import Client
from pyrogram.types import InlineKeyboardMarkup as PInlineKeyboardMarkup, InlineKeyboardButton as PInlineKeyboardButton
from pyrogram.errors import FloodWait
from pyrogram.enums import ParseMode

from app.core.config import settings
from app.core.db import init_db
from app.core.security import decrypt
from bson import ObjectId

logger = logging.getLogger(__name__)


class SkipChat(Exception):
    pass


def _type_name(t) -> str:
    try:
        return str(t).split('.')[-1].lower()
    except Exception:
        return (str(t) if t is not None else "").lower()


def _to_bool(v) -> bool:
    if isinstance(v, bool):
        return v
    if isinstance(v, (int, float)):
        return v != 0
    if isinstance(v, str):
        s = v.strip().lower()
        return s in {"1", "true", "on", "yes", "y"}
    return bool(v)


class WorkerSettings:
    # Parse REDIS_URL and configure SSL explicitly (works for Redis Cloud)
    _u = urlparse(settings.REDIS_URL)
    _db = 0
    if _u.path and len(_u.path) > 1:
        try:
            _db = int(_u.path.lstrip('/').split('/')[0])
        except Exception:
            _db = 0
    _use_ssl = _u.scheme == 'rediss'
    # Accept relaxed certs if requested via query (ssl_cert_reqs=none)
    _ssl_reqs = None
    if 'ssl_cert_reqs=none' in (_u.query or '').lower():
        _ssl_reqs = _ssl.CERT_NONE
    redis_settings = RedisSettings(
        host=_u.hostname or 'localhost',
        port=_u.port or 6379,
        password=_u.password,
        database=_db,
        ssl=_use_ssl,
        ssl_cert_reqs=_ssl_reqs,
    )
    burst = False


async def startup(ctx):
    logger.info("Worker startup")
    ctx["db"] = await init_db()


async def shutdown(ctx):
    logger.info("Worker shutdown")


async def dispatch_ad(ctx, job: dict):
    # Placeholder for ad dispatch logic; will use Pyrogram client session and send messages with backoff
    logger.info("Dispatching job: %s", job)
    await asyncio.sleep(0.01)
    return {"ok": True}


async def _warm_up_dialogs(client: Client, max_load: int = 500) -> int:
    """Iterate dialogs to populate peer cache so integer IDs resolve reliably."""
    loaded = 0
    try:
        async for _ in client.get_dialogs():
            loaded += 1
            if loaded >= max_load:
                break
    except Exception as e:
        logger.warning(f"Dialog warm-up failed: {e}")
    return loaded


async def _send_via_account(client: Client, msg: Dict[str, Any], chat_id: int, allowed_types: Set[str] | None = None) -> None:
    # Ensure connection and resolve peer robustly
    logger.info(f"Attempting to send to {chat_id}, client connected: {client.is_connected}")
    if not client.is_connected:
        logger.warning(f"Client not connected when trying to send to {chat_id}, reconnecting...")
        await client.connect()
        logger.info(f"Reconnected client, status: {client.is_connected}")

    # Build candidate peer ids to try
    candidates: List[int] = []
    try:
        base = int(chat_id)
        s = str(base)
        candidates.append(base)
        if s.startswith("-100"):
            alt = int(s[4:])
            candidates.extend([alt, -alt])
        else:
            abs_id = abs(base)
            if abs_id < 10**12:
                # Try Bot API styled id and legacy negative form
                candidates.extend([-(10**12) - abs_id, -abs_id, abs_id])
    except Exception:
        candidates = [chat_id]

    resolved = None
    resolved_type = None
    for _pass in (1, 2):
        for pid in candidates:
            try:
                info = await client.get_chat(pid)
                tname = _type_name(info.type)
                logger.info(f"Resolved peer {pid}: {tname} - {getattr(info, 'title', 'Unknown')}")
                # Respect allowed chat-types if provided
                if allowed_types is not None and tname not in allowed_types:
                    raise SkipChat(f"type_disabled:{tname}")
                resolved = pid
                resolved_type = tname
                break
            except Exception as e:
                logger.debug(f"Peer resolve failed with {pid}: {e}")
        if resolved is not None:
            break
        # Warm up dialogs to populate peer cache, then retry once
        loaded = await _warm_up_dialogs(client, 1000)
        logger.info(f"Warmed up dialogs: loaded={loaded}")

    if resolved is None:
        # Signal to the caller that this chat should be skipped
        raise SkipChat(f"Peer resolution failed for {chat_id}")
    chat_id = resolved
    
    text = msg.get("text") or ""
    buttons = msg.get("buttons") or []
    media = msg.get("media") or None
    rk = None
    if buttons:
        rk = PInlineKeyboardMarkup(
            [[PInlineKeyboardButton(b.get("text"), url=b.get("url")) for b in row if b.get("url")] for row in buttons]
        )
    
    try:
        if media and media.get("type") == "photo":
            await client.send_photo(chat_id, media.get("path"), caption=text, parse_mode=ParseMode.HTML, reply_markup=rk)
        elif media and media.get("type") == "document":
            await client.send_document(chat_id, media.get("path"), caption=text, parse_mode=ParseMode.HTML, reply_markup=rk)
        elif media and media.get("type") == "video":
            await client.send_video(chat_id, media.get("path"), caption=text, parse_mode=ParseMode.HTML, reply_markup=rk)
        else:
            # Send HTML-formatted text + URL buttons.
            await client.send_message(chat_id, text, parse_mode=ParseMode.HTML, reply_markup=rk, disable_web_page_preview=False)
        
        logger.info(f"Successfully sent message to {chat_id}")
        
    except Exception as e:
        logger.error(f"Send failed to {chat_id}: {type(e).__name__}: {e}")
        # Try reconnecting and sending once more
        if not client.is_connected:
            await client.connect()
            if media and media.get("type") == "photo":
                await client.send_photo(chat_id, media.get("path"), caption=text, parse_mode=ParseMode.HTML, reply_markup=rk)
        else:
            raise


async def send_campaign(ctx, campaign_id: str):
    db = ctx["db"]
    campaigns = db.campaigns
    accounts_col = db.accounts
    logs = db.logs
    oid = ObjectId(campaign_id) if not isinstance(campaign_id, ObjectId) else campaign_id
    campaign = await campaigns.find_one({"_id": oid})
    if not campaign:
        logger.warning("Campaign not found: %s", campaign_id)
        return {"ok": False, "error": "not_found"}
    cid_str = str(oid)
    owner = campaign["owner_user_id"]
    targets: List[int] = campaign.get("targets", [])
    msg = campaign.get("message", {})
    rate_per_min = int(campaign.get("rate_per_min", settings.INTERVAL_PRESETS_DEFAULT))
    delay = max(60 / max(rate_per_min, 1), 1.0)
    mode = campaign.get("mode", "include")
    # Allowed chat types from campaign (default: all enabled)
    types_cfg = campaign.get("types") or {"private": True, "group": True, "supergroup": True, "channel": True}
    allowed_types: Set[str] = {k for k, v in types_cfg.items() if _to_bool(v)}
    exclude: Set[int] = set(campaign.get("exclude", []) or [])

    # Prepare clients for all owner's accounts
    accounts = accounts_col.find({"owner_user_id": owner, "is_active": True})
    clients: List[Client] = []
    account_count = 0
    
    async for acc in accounts:
        account_count += 1
        logger.info(f"Found account for user {owner}: {acc.get('phone', 'unknown')} (active: {acc.get('is_active')})")
        try:
            session = decrypt(acc["session_string"])
            c = Client(
                name=f":memory:{acc['_id']}", 
                api_id=settings.API_ID, 
                api_hash=settings.API_HASH, 
                session_string=session,
                no_updates=True,  # CRITICAL: Disable updates to prevent auto-disconnect
                sleep_threshold=60,  # Keep connection alive longer
                workdir=":memory:"  # Use in-memory storage
            )
            await c.connect()

            # Warm up dialogs to meet peers and populate cache
            try:
                loaded = await _warm_up_dialogs(c, 1000)
                logger.info(f"Account {acc.get('phone', 'unknown')} warm-up loaded dialogs: {loaded}")
            except Exception as e:
                logger.warning(f"Warm-up failed for {acc.get('phone', 'unknown')}: {e}")

            # CRITICAL: Test connection immediately after connecting
            try:
                me = await c.get_me()
                logger.info(f"Connected account {acc.get('phone', 'unknown')} - User: {me.first_name} (@{me.username}) - Status: {c.is_connected}")
                clients.append(c)
            except Exception as test_error:
                logger.error(f"Connection test failed for {acc.get('phone', 'unknown')}: {test_error}")
                await c.disconnect()
                continue
            await logs.insert_one({"owner_user_id": owner, "campaign_id": cid_str, "ts": datetime.now(timezone.utc), "event": "client_connect", "phone": acc.get('phone', 'unknown')})
        except Exception as e:
            logger.error(f"Failed to connect account {acc.get('phone', 'unknown')}: {e}")
            await logs.insert_one({"owner_user_id": owner, "campaign_id": cid_str, "ts": datetime.now(timezone.utc), "event": "client_connect_fail", "error": str(e), "phone": acc.get('phone', 'unknown')})

    logger.info(f"User {owner}: Found {account_count} accounts in DB, {len(clients)} connected successfully")
    
    if not clients:
        return {"ok": False, "error": "no_accounts"}

    async def discover_dialog_ids(client: Client) -> List[int]:
        ids: List[int] = []
        async for dialog in client.get_dialogs():
            try:
                cid = int(dialog.chat.id)
                tname = _type_name(dialog.chat.type)
                if cid in exclude:
                    continue
                if allowed_types and tname not in allowed_types:
                    continue
                ids.append(cid)
            except Exception:
                continue
        return ids

    if mode == "all":
        # Discover targets across first account; others will share split of same list
        if clients:
            try:
                targets = await discover_dialog_ids(clients[0])
                logger.info(f"Discovered {len(targets)} dialogs for user {owner}")
            except Exception as e:
                logger.error(f"Failed to discover dialogs for user {owner}: {e}")
                await logs.insert_one({"owner_user_id": owner, "campaign_id": cid_str, "ts": datetime.now(timezone.utc), "event": "discover_fail", "error": str(e)})
                targets = []
    
    logger.info(f"Campaign {campaign_id}: mode={mode}, targets={len(targets)}, exclude={len(exclude)}, message_keys={list(msg.keys()) if msg else 'None'}")

    def classify_fail_reason(exc: Exception) -> str:
        s = f"{type(exc).__name__}: {exc}".upper()
        if "PEER_FLOOD" in s:
            return "limit"
        if "FLOOD_WAIT" in s or "FLOODWAIT" in s:
            return "floodwait"
        if "BOT" in s and "START" in s:
            return "requires_start"
        if "FORBIDDEN" in s and "SEND_MESSAGE" in s:
            return "forbidden/not_allowed"
        if "FORBIDDEN" in s or "BLOCK" in s:
            return "forbidden/blocked"
        if "USER_NOT_PARTICIPANT" in s or "CHANNEL_PRIVATE" in s or "INVITE" in s:
            return "forbidden/not_member"
        if "MUTE" in s or "RESTRICT" in s:
            return "muted/restricted"
        if "INPUT_USER_DEACTIVATED" in s:
            return "user/deactivated"
        if "PEER_ID_INVALID" in s or "PARTICIPANT_ID_INVALID" in s:
            return "peer/invalid"
        if "SLOWMODE" in s:
            return "rate/slowmode"
        return "unknown"

    async def worker_loop(client: Client, subset: List[int]):
        logger.info(f"Worker loop starting with {len(subset)} targets")
        
        # CRITICAL: Ensure client stays connected throughout the loop
        if not client.is_connected:
            logger.info("Connecting client for worker loop")
            await client.connect()
        
        for i, chat_id in enumerate(subset):
            logger.info(f"Processing target {i+1}/{len(subset)}: {chat_id}")
            
            # Verify client connection before each send
            if not client.is_connected:
                logger.warning("Client disconnected during loop, reconnecting")
                await client.connect()
                # Test connection after reconnect
                try:
                    await client.get_me()
                    logger.info("Reconnection successful")
                except Exception as e:
                    logger.error(f"Reconnection failed: {e}")
                    break
            
            # check if campaign is still running before each send
            cur = await campaigns.find_one({"_id": oid}, projection={"status": 1})
            if not cur or cur.get("status") not in {"running"}:
                logger.info(f"Campaign stopped, breaking loop")
                break
            try:
                # Attempt log with metadata
                chat_title = None
                chat_type = None
                try:
                    info = await client.get_chat(chat_id)
                    chat_type = _type_name(getattr(info, 'type', None))
                    chat_title = getattr(info, 'title', None)
                except Exception:
                    pass
                if allowed_types and chat_type and chat_type not in allowed_types:
                    await logs.insert_one({"owner_user_id": owner, "campaign_id": cid_str, "ts": datetime.now(timezone.utc), "event": "skipped", "chat_id": chat_id, "reason": f"type_disabled:{chat_type}"})
                    continue
                await logs.insert_one({"owner_user_id": owner, "campaign_id": cid_str, "ts": datetime.now(timezone.utc), "event": "attempt", "chat_id": chat_id, "chat_type": chat_type, "chat_title": chat_title})

                logger.info(f"Sending message to {chat_id}")
                await _send_via_account(client, msg, chat_id, allowed_types)
                logger.info(f"Successfully sent to {chat_id}")
                await logs.insert_one({"owner_user_id": owner, "campaign_id": cid_str, "ts": datetime.now(timezone.utc), "event": "sent", "chat_id": chat_id, "chat_type": chat_type, "chat_title": chat_title})
            except FloodWait as fw:
                await logs.insert_one({"owner_user_id": owner, "campaign_id": cid_str, "ts": datetime.now(timezone.utc), "event": "floodwait", "chat_id": chat_id, "seconds": fw.value})
                await asyncio.sleep(fw.value + 1)
                # retry once
                try:
                    await _send_via_account(client, msg, chat_id, allowed_types)
                    await logs.insert_one({"owner_user_id": owner, "campaign_id": cid_str, "ts": datetime.now(timezone.utc), "event": "sent_after_fw", "chat_id": chat_id})
                except Exception as e:
                    await logs.insert_one({"owner_user_id": owner, "campaign_id": cid_str, "ts": datetime.now(timezone.utc), "event": "failed", "chat_id": chat_id, "error": str(e), "fail_reason": classify_fail_reason(e)})
            except SkipChat as s:
                reason = str(s)
                logger.info(f"Skipped {chat_id}: {reason}")
                await logs.insert_one({"owner_user_id": owner, "campaign_id": cid_str, "ts": datetime.now(timezone.utc), "event": "skipped", "chat_id": chat_id, "reason": reason, "chat_type": chat_type, "chat_title": chat_title})
            except Exception as e:
                logger.error(f"Failed to send to {chat_id}: {type(e).__name__}: {e}")
                await logs.insert_one({"owner_user_id": owner, "campaign_id": cid_str, "ts": datetime.now(timezone.utc), "event": "failed", "chat_id": chat_id, "error": f"{type(e).__name__}: {str(e)}", "chat_type": chat_type, "chat_title": chat_title, "fail_reason": classify_fail_reason(e)})
            await asyncio.sleep(delay)

    # Apply exclude to targets if provided
    if exclude:
        original_count = len(targets)
        targets = [t for t in targets if t not in exclude]
        logger.info(f"Filtered targets: {original_count} -> {len(targets)} (excluded {original_count - len(targets)})")

    if not targets:
        logger.warning(f"No targets to send to for campaign {campaign_id}")
        await campaigns.update_one({"_id": oid}, {"$set": {"status": "completed", "completed_at": datetime.now(timezone.utc)}})
        return {"ok": True, "message": "No targets found"}

    # Split targets roughly evenly among accounts
    chunks: List[List[int]] = [[] for _ in range(len(clients))]
    for i, t in enumerate(targets):
        chunks[i % len(clients)].append(t)
    
    logger.info(f"Starting to send to {len(targets)} targets using {len(clients)} accounts")
    for i, chunk in enumerate(chunks):
        logger.info(f"Account {i+1}: {len(chunk)} targets")
    
    tasks = [worker_loop(clients[i], chunks[i]) for i in range(len(clients))]
    await asyncio.gather(*tasks)
    
    # CRITICAL: Properly disconnect all clients to prevent resource leaks
    for client in clients:
        try:
            if client.is_connected:
                await client.disconnect()
                logger.info(f"Disconnected client {client.name}")
        except Exception as e:
            logger.warning(f"Error disconnecting client: {e}")
    
    # Mark campaign as completed
    await campaigns.update_one({"_id": oid}, {"$set": {"status": "completed", "completed_at": datetime.now(timezone.utc)}})
    
    return {"ok": True}


class Settings(WorkerSettings):
    functions = [dispatch_ad, send_campaign]
    on_startup = startup
    on_shutdown = shutdown
    job_timeout = 3600
