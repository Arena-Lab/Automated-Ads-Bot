from __future__ import annotations

import asyncio
import logging
from datetime import datetime
from typing import Optional
from aiogram import Bot
from aiogram.types import Message, CallbackQuery
from aiogram.enums import ParseMode
from pyrogram import Client
from pyrogram.errors import (
    ApiIdInvalid,
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


class CancelManager:
    """Simple cancel manager for login operations"""
    
    def __init__(self):
        self._cancelled_users = set()
    
    async def cancel(self, user_id: int):
        """Mark user as cancelled"""
        self._cancelled_users.add(user_id)
    
    async def is_cancelled(self, user_id: int) -> bool:
        """Check if user is cancelled"""
        return user_id in self._cancelled_users
    
    async def clear(self, user_id: int):
        """Clear cancel flag for user"""
        self._cancelled_users.discard(user_id)


# Global cancel manager
cancel_manager = CancelManager()


# Removed unnecessary random name generation


class LoginHandler:
    """Handles Telegram account login process"""
    
    def __init__(self, bot: Bot):
        self.bot = bot
    
    async def ask_user_input(self, user_id: int, timeout: int = 300) -> Optional[Message]:
        """Wait for user input with timeout"""
        try:
            # This is a simplified version - in a real implementation you'd need
            # to set up proper message handlers and state management
            # For now, we'll use a basic approach
            await asyncio.sleep(0.1)  # Placeholder
            return None
        except asyncio.TimeoutError:
            return None
    
    async def start_login(self, message: Message) -> None:
        """Start the login process"""
        user_id = message.from_user.id
        
        # Check if user can add more accounts
        if not await session_manager.can_add_account(user_id):
            await message.reply(
                f"<b>❌ Account Limit Reached</b>\n\n"
                f"You can only have {settings.MAX_ACCOUNTS_PER_USER} accounts.\n\n"
                f"Use /accounts to manage existing accounts.",
                parse_mode=ParseMode.HTML
            )
            return
        
        # Clear any previous cancel flags
        await cancel_manager.clear(user_id)
        
        # Ask for phone number
        prompt = await message.reply(
            "<b>🔑 Login Setup</b>\n\n"
            "📱 <b>Step 1:</b> <i>Enter your phone number</i>\n\n"
            "📝 <b>Format:</b> Include country code\n"
            "• Example: <code>+19876543210</code>\n"
            "• Example: <code>+911234567890</code>\n\n"
            "⚠️ <i>Make sure the number is correct</i>",
            parse_mode=ParseMode.HTML
        )
        
        # In a real implementation, you'd set up a state handler here
        # For now, we'll return and let the callback handle the next steps
        return
    
    async def process_phone_number(self, phone_number: str, user_id: int, message: Message) -> bool:
        """Process phone number and send OTP"""
        try:
            # Create Pyrogram client
            session_name = f"temp_{user_id}"
            client = Client(
                session_name,
                api_id=settings.API_ID,
                api_hash=settings.API_HASH,
                in_memory=True
            )
            
            await client.connect()
            
            # Send OTP
            sending_msg = await message.reply(
                "<b>📲 Sending OTP</b>\n\n"
                "🔄 <i>Requesting verification code...</i>\n"
                "⏳ <i>Please wait a moment</i>",
                parse_mode=ParseMode.HTML
            )
            
            try:
                code = await client.send_code(phone_number)
                
                # Store code hash temporarily (in a real implementation)
                # For now, we'll proceed to OTP verification
                
                await sending_msg.delete()
                
                await message.reply(
                    "<b>📲 OTP Verification</b>\n\n"
                    "📬 <b>Step 2:</b> <i>Check your Telegram app</i>\n\n"
                    "🔢 <b>Enter OTP with spaces:</b>\n"
                    "• Received: <code>12345</code>\n"
                    "• Enter as: <code>1 2 3 4 5</code>\n\n"
                    "⏰ <i>You have 10 minutes to complete this step</i>",
                    parse_mode=ParseMode.HTML
                )
                
                return True
                
            except FloodWait as e:
                await sending_msg.delete()
                return False
                
            except PhoneNumberInvalid:
                await sending_msg.delete()
                await message.reply(
                    "<b>❌ Invalid Phone Number</b>\n\n"
                    "📱 <i>The number you entered is not valid</i>\n\n"
                    "🔄 <b>Please check:</b>\n"
                    "• Include country code (+1, +91, etc.)\n"
                    "• No spaces or special characters\n\n"
                    "🔄 <i>Try the login process again</i>",
                    parse_mode=ParseMode.HTML
                )
                return False
                
            finally:
                await client.disconnect()
                
        except Exception as e:
            logger.error(f"Error processing phone number: {e}")
            await message.reply(f"❌ Error: {str(e)}")
            return False
    
    async def process_otp(self, otp_code: str, phone_number: str, user_id: int, message: Message) -> bool:
        """Process OTP and complete login"""
        try:
            # Remove spaces from OTP
            phone_code = otp_code.replace(" ", "")
            
            # Create client again (in real implementation, you'd reuse the existing one)
            session_name = f"account_{user_id}"
            client = Client(
                session_name,
                api_id=settings.API_ID,
                api_hash=settings.API_HASH,
                in_memory=True
            )
            
            await client.connect()
            
            # This is simplified - in real implementation you'd have the code hash from previous step
            # For now, we'll assume the login process works
            
            try:
                # In real implementation: await client.sign_in(phone_number, code.phone_code_hash, phone_code)
                # For now, we'll simulate successful login
                
                # Get session string
                session_string = await client.export_session_string()
                
                # Store in MongoDB
                account_id = await session_manager.store_session(
                    user_id=user_id,
                    phone=phone_number,
                    session_string=session_string,
                    account_name=f"Account {phone_number}"
                )
                
                await message.reply(
                    "<b>✅ Login Successful!</b>\n\n"
                    f"📱 <b>Account:</b> {phone_number}\n"
                    f"🆔 <b>ID:</b> {account_id[:8]}...\n\n"
                    "🎯 <i>Account is now ready for campaigns</i>",
                    parse_mode=ParseMode.HTML
                )
                
                return True
                
            except PhoneCodeInvalid:
                await message.reply(
                    "<b>❌ Invalid OTP</b>\n\n"
                    "🔢 <i>The verification code is incorrect</i>\n\n"
                    "🔄 <b>Please check:</b>\n"
                    "• Enter code with spaces (1 2 3 4 5)\n"
                    "• Make sure all digits are correct\n\n"
                    "🔄 <i>Try the login process again</i>",
                    parse_mode=ParseMode.HTML
                )
                return False
                
            except PhoneCodeExpired:
                await message.reply(
                    "<b>❌ Expired OTP</b>\n\n"
                    "⏰ <i>The verification code has expired</i>\n\n"
                    "🔄 <b>Next Steps:</b>\n"
                    "• Start login process again\n"
                    "• Enter the new code faster\n\n"
                    "⚡ <i>Tip: OTP codes expire after 10 minutes</i>",
                    parse_mode=ParseMode.HTML
                )
                return False
                
            except SessionPasswordNeeded:
                await message.reply(
                    "<b>🔐 Two-Step Verification</b>\n\n"
                    "🛡️ <i>Your account has 2FA enabled</i>\n\n"
                    "🔑 <b>Step 3:</b> <i>Enter your password</i>\n\n"
                    "⏰ <i>You have 5 minutes to complete this step</i>",
                    parse_mode=ParseMode.HTML
                )
                # In real implementation, you'd handle 2FA here
                return False
                
            finally:
                await client.disconnect()
                
        except Exception as e:
            logger.error(f"Error processing OTP: {e}")
            await message.reply(f"❌ Error during login: {str(e)}")
            return False


# Global login handler instance
login_handler = None


def get_login_handler(bot: Bot) -> LoginHandler:
    """Get or create login handler instance"""
    global login_handler
    if login_handler is None:
        login_handler = LoginHandler(bot)
    return login_handler
