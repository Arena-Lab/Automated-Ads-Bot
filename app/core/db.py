from __future__ import annotations

from motor.motor_asyncio import AsyncIOMotorClient, AsyncIOMotorDatabase
from .config import settings

_client: AsyncIOMotorClient | None = None
_db: AsyncIOMotorDatabase | None = None


async def init_db() -> AsyncIOMotorDatabase:
    global _client, _db
    if _db is None:
        _client = AsyncIOMotorClient(settings.MONGO_URI)
        _db = _client[settings.MONGO_DB]
        await ensure_indexes(_db)
    return _db


aSYNC_INDEX_CREATED = False


async def ensure_indexes(db: AsyncIOMotorDatabase) -> None:
    global aSYNC_INDEX_CREATED
    if aSYNC_INDEX_CREATED:
        return
    await db.users.create_index("user_id", unique=True)
    await db.accounts.create_index([("owner_user_id", 1)])
    await db.accounts.create_index([("phone", 1)], unique=True, sparse=True)
    await db.campaigns.create_index([("owner_user_id", 1), ("status", 1)])
    await db.jobs.create_index([("campaign_id", 1), ("state", 1)])
    await db.logs.create_index([("owner_user_id", 1), ("ts", -1)])
    aSYNC_INDEX_CREATED = True


def get_db_sync() -> AsyncIOMotorDatabase:
    # For contexts where event loop not available; ensure init_db() is awaited in app startup.
    if _db is None:
        raise RuntimeError("DB not initialized. Call init_db() during startup.")
    return _db
