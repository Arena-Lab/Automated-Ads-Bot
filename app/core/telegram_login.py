from __future__ import annotations

import asyncio
import logging
from datetime import datetime, timezone
from typing import Optional, Dict, Any
from pyrogram import Client
from pyrogram.errors import (
    PhoneNumberInvalid,
    PhoneCodeInvalid,
    PhoneCodeExpired,
    SessionPasswordNeeded,
    PasswordHashInvalid,
    FloodWait
)

from .config import settings
from .session_manager import session_manager

logger = logging.getLogger(__name__)


class TelegramLoginManager:
    """Manages real Telegram login process using Pyrogram"""
    
    def __init__(self):
        self.active_sessions: Dict[int, Dict[str, Any]] = {}
    
    async def start_login_process(self, user_id: int, phone_number: str) -> Dict[str, Any]:
        """Start the login process and send OTP"""
        try:
            # Create temporary client
            session_name = f"temp_login_{user_id}"
            client = Client(
                session_name,
                api_id=settings.API_ID,
                api_hash=settings.API_HASH,
                in_memory=True
            )
            
            await client.connect()
            
            # Send OTP
            code_info = await client.send_code(phone_number)
            
            # Store session info temporarily
            self.active_sessions[user_id] = {
                "client": client,
                "phone": phone_number,
                "code_hash": code_info.phone_code_hash,
                "session_name": session_name,
                "started_at": datetime.now(timezone.utc)
            }
            
            return {
                "success": True,
                "message": "OTP sent successfully",
                "phone_code_hash": code_info.phone_code_hash
            }
            
        except FloodWait as e:
            if client:
                await client.disconnect()
            wait_min = e.value // 60
            wait_sec = e.value % 60
            return {
                "success": False,
                "error": "flood_wait",
                "wait_time": e.value,
                "message": f"Rate limited. Wait {wait_min}m {wait_sec}s"
            }
            
        except PhoneNumberInvalid:
            if client:
                await client.disconnect()
            return {
                "success": False,
                "error": "invalid_phone",
                "message": "Invalid phone number format"
            }
            
        except Exception as e:
            if client:
                await client.disconnect()
            logger.error(f"Error starting login for {user_id}: {e}")
            return {
                "success": False,
                "error": "unknown",
                "message": f"Error: {str(e)}"
            }
    
    async def verify_otp(self, user_id: int, otp_code: str) -> Dict[str, Any]:
        """Verify OTP and complete login"""
        if user_id not in self.active_sessions:
            return {
                "success": False,
                "error": "no_session",
                "message": "No active login session found"
            }
        
        session_info = self.active_sessions[user_id]
        client = session_info["client"]
        phone = session_info["phone"]
        code_hash = session_info["code_hash"]
        
        try:
            # Clean OTP (remove spaces)
            clean_otp = otp_code.replace(" ", "")
            
            # Sign in with OTP
            await client.sign_in(phone, code_hash, clean_otp)
            
            # Get session string
            session_string = await client.export_session_string()
            
            # Store in MongoDB
            account_id = await session_manager.store_session(
                user_id=user_id,
                phone=phone,
                session_string=session_string,
                account_name=f"Account {phone}"
            )
            
            # Clean up
            await client.disconnect()
            del self.active_sessions[user_id]
            
            return {
                "success": True,
                "message": "Login successful",
                "account_id": account_id,
                "phone": phone
            }
            
        except PhoneCodeInvalid:
            return {
                "success": False,
                "error": "invalid_otp",
                "message": "Invalid OTP code"
            }
            
        except PhoneCodeExpired:
            # Clean up expired session
            await client.disconnect()
            del self.active_sessions[user_id]
            return {
                "success": False,
                "error": "expired_otp",
                "message": "OTP code has expired"
            }
            
        except SessionPasswordNeeded:
            # 2FA required
            return {
                "success": False,
                "error": "2fa_required",
                "message": "Two-factor authentication required",
                "needs_password": True
            }
            
        except Exception as e:
            logger.error(f"Error verifying OTP for {user_id}: {e}")
            return {
                "success": False,
                "error": "unknown",
                "message": f"Error: {str(e)}"
            }
    
    async def verify_2fa_password(self, user_id: int, password: str) -> Dict[str, Any]:
        """Verify 2FA password"""
        if user_id not in self.active_sessions:
            return {
                "success": False,
                "error": "no_session",
                "message": "No active login session found"
            }
        
        session_info = self.active_sessions[user_id]
        client = session_info["client"]
        phone = session_info["phone"]
        
        try:
            # Check password
            await client.check_password(password)
            
            # Get session string
            session_string = await client.export_session_string()
            
            # Store in MongoDB
            account_id = await session_manager.store_session(
                user_id=user_id,
                phone=phone,
                session_string=session_string,
                account_name=f"Account {phone}"
            )
            
            # Clean up
            await client.disconnect()
            del self.active_sessions[user_id]
            
            return {
                "success": True,
                "message": "Login successful with 2FA",
                "account_id": account_id,
                "phone": phone
            }
            
        except PasswordHashInvalid:
            return {
                "success": False,
                "error": "invalid_password",
                "message": "Invalid 2FA password"
            }
            
        except Exception as e:
            logger.error(f"Error verifying 2FA for {user_id}: {e}")
            return {
                "success": False,
                "error": "unknown",
                "message": f"Error: {str(e)}"
            }
    
    async def cancel_login(self, user_id: int) -> bool:
        """Cancel active login session"""
        if user_id in self.active_sessions:
            try:
                client = self.active_sessions[user_id]["client"]
                await client.disconnect()
                del self.active_sessions[user_id]
                return True
            except Exception as e:
                logger.error(f"Error cancelling login for {user_id}: {e}")
        return False
    
    async def cleanup_expired_sessions(self):
        """Clean up expired login sessions (older than 15 minutes)"""
        current_time = datetime.now(timezone.utc)
        expired_users = []
        
        for user_id, session_info in self.active_sessions.items():
            if (current_time - session_info["started_at"]).total_seconds() > 900:  # 15 minutes
                expired_users.append(user_id)
        
        for user_id in expired_users:
            await self.cancel_login(user_id)
    
    async def test_account_connection(self, user_id: int, phone: str) -> Dict[str, Any]:
        """Test if a stored account session is still valid"""
        try:
            session_string = await session_manager.get_session_string(user_id, phone)
            if not session_string:
                return {
                    "success": False,
                    "error": "no_session",
                    "message": "No session found for this account"
                }
            
            # Create client with stored session
            client = Client(
                f"test_{user_id}_{phone}",
                api_id=settings.API_ID,
                api_hash=settings.API_HASH,
                session_string=session_string
            )
            
            await client.connect()
            
            # Try to get account info
            me = await client.get_me()
            
            await client.disconnect()
            
            # Update last used timestamp
            await session_manager.update_last_used(user_id, phone)
            
            return {
                "success": True,
                "message": "Account connection successful",
                "account_info": {
                    "id": me.id,
                    "first_name": me.first_name,
                    "last_name": me.last_name,
                    "username": me.username,
                    "phone": me.phone_number
                }
            }
            
        except Exception as e:
            logger.error(f"Error testing account {phone} for user {user_id}: {e}")
            return {
                "success": False,
                "error": "connection_failed",
                "message": f"Connection test failed: {str(e)}"
            }


# Global login manager instance
telegram_login_manager = TelegramLoginManager()
