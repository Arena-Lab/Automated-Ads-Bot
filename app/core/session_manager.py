from __future__ import annotations

import asyncio
import logging
from datetime import datetime, timezone
from typing import Dict, List, Optional, Any
from motor.motor_asyncio import AsyncIOMotorDatabase
from pyrogram import Client
from pyrogram.errors import SessionPasswordNeeded, PhoneCodeInvalid, PhoneCodeExpired, PasswordHashInvalid, FloodWait, PhoneNumberInvalid
from .db import get_db_sync
from .config import settings
from .security import encrypt, decrypt

logger = logging.getLogger(__name__)


class SessionManager:
    """Manages Telegram account sessions with MongoDB storage"""
    
    def __init__(self):
        self.db = None
    
    async def get_db(self) -> AsyncIOMotorDatabase:
        """Get database instance"""
        if self.db is None:
            self.db = get_db_sync()
        return self.db
    
    async def store_session(self, user_id: int, phone: str, session_string: str, account_name: str = None) -> str:
        """Store a Telegram session in MongoDB"""
        db = await self.get_db()
        
        account_data = {
            "owner_user_id": user_id,
            "phone": phone,
            "session_string": encrypt(session_string),  # Encrypt session before storing
            "account_name": account_name or f"Account {phone}",
            "created_at": datetime.now(timezone.utc),
            "last_used": datetime.now(timezone.utc),
            "is_active": True,
            "login_attempts": 0,
            "status": "active"
        }
        
        # Check if account already exists
        existing = await db.accounts.find_one({"phone": phone})
        if existing:
            # Update existing account
            await db.accounts.update_one(
                {"phone": phone},
                {"$set": {
                    "session_string": encrypt(session_string),  # Encrypt session before storing
                    "last_used": datetime.now(timezone.utc),
                    "is_active": True,
                    "status": "active"
                }}
            )
            return str(existing["_id"])
        else:
            # Insert new account
            result = await db.accounts.insert_one(account_data)
            return str(result.inserted_id)
    
    async def get_user_sessions(self, user_id: int) -> List[Dict[str, Any]]:
        """Get all active sessions for a user"""
        db = await self.get_db()
        
        cursor = db.accounts.find({
            "owner_user_id": user_id,
            "is_active": True
        }).sort("created_at", -1)
        
        sessions = []
        async for doc in cursor:
            sessions.append({
                "id": str(doc["_id"]),
                "phone": doc["phone"],
                "account_name": doc.get("account_name", f"Account {doc['phone']}"),
                "created_at": doc["created_at"],
                "last_used": doc.get("last_used", doc["created_at"]),
                "status": doc.get("status", "active")
            })
        
        return sessions
    
    async def get_session_string(self, user_id: int, phone: str) -> Optional[str]:
        """Get session string for a specific phone number"""
        db = await self.get_db()
        
        account = await db.accounts.find_one({
            "owner_user_id": user_id,
            "phone": phone,
            "is_active": True
        })
        
        if account:
            return decrypt(account["session_string"])  # Decrypt session when retrieving
        return None
    
    async def deactivate_session(self, user_id: int, phone: str) -> bool:
        """Deactivate a session"""
        db = await self.get_db()
        
        result = await db.accounts.update_one(
            {"owner_user_id": user_id, "phone": phone},
            {"$set": {"is_active": False, "status": "deactivated"}}
        )
        
        return result.modified_count > 0
    
    async def delete_session(self, user_id: int, phone: str) -> bool:
        """Delete a session completely"""
        db = await self.get_db()
        
        result = await db.accounts.delete_one({
            "owner_user_id": user_id,
            "phone": phone
        })
        
        return result.deleted_count > 0
    
    async def update_last_used(self, user_id: int, phone: str):
        """Update last used timestamp for a session"""
        db = await self.get_db()
        
        await db.accounts.update_one(
            {"owner_user_id": user_id, "phone": phone},
            {"$set": {"last_used": datetime.now(timezone.utc)}}
        )
    
    async def get_account_count(self, user_id: int) -> int:
        """Get count of active accounts for a user"""
        db = await self.get_db()
        
        count = await db.accounts.count_documents({
            "owner_user_id": user_id,
            "is_active": True
        })
        
        return count
    
    async def can_add_account(self, user_id: int) -> bool:
        """Check if user can add more accounts"""
        current_count = await self.get_account_count(user_id)
        return current_count < settings.MAX_ACCOUNTS_PER_USER
    
    async def create_client(self, user_id: int, phone: str) -> Optional[Client]:
        """Create a Pyrogram client from stored session"""
        session_string = await self.get_session_string(user_id, phone)
        if not session_string:
            return None
        
        try:
            client = Client(
                name=f"account_{user_id}_{phone}",
                api_id=settings.API_ID,
                api_hash=settings.API_HASH,
                session_string=session_string
            )
            return client
        except Exception as e:
            logger.error(f"Failed to create client for {phone}: {e}")
            return None


# Global session manager instance
session_manager = SessionManager()
