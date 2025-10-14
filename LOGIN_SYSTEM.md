# 🔑 Telegram Login System

## Overview

This enterprise-level Telegram bot now includes a comprehensive login system that allows users to authenticate their Telegram accounts and store sessions securely in MongoDB. The system supports multiple accounts per user, 2FA authentication, and persistent sessions that survive bot restarts.

## ✨ Features

### 🔐 Secure Authentication
- **Real Telegram Login**: Uses Pyrogram for authentic Telegram API authentication
- **OTP with Spaces**: Implements spaced OTP input (1 2 3 4 5) to avoid Telegram expiry
- **2FA Support**: Full two-factor authentication support
- **Session Encryption**: Sessions are securely stored in MongoDB

### 👥 Multi-Account Management
- **Multiple Accounts**: Users can add up to 3 accounts (configurable)
- **Account Overview**: View all logged-in accounts with status
- **Individual Management**: Test, logout, or delete specific accounts
- **Session Persistence**: Sessions survive bot restarts

### 🛡️ Security & Reliability
- **Automatic Cleanup**: Expired login sessions are cleaned up automatically
- **Error Handling**: Comprehensive error handling for all scenarios
- **Rate Limiting**: Respects Telegram's rate limits and flood wait
- **Session Validation**: Test account connections to verify validity

## 🎯 User Interface

### Main Menu (Updated)
```
🔑 Login          👥 My Accounts
📝 Set Message    🎯 Targets
⏱️ Interval       ▶️ Start Ads
📊 Analytics      🤖 Auto Reply
📜 Policy
```

### Login Flow
1. **Start Login** → Enter phone number with country code
2. **OTP Verification** → Enter code with spaces (1 2 3 4 5)
3. **2FA (if enabled)** → Enter Telegram password
4. **Success** → Account stored and ready for campaigns

### Account Management
- **View Accounts**: See all logged-in accounts with status
- **Test Connection**: Verify account is still valid
- **Logout Account**: Deactivate session (keeps in database)
- **Delete Account**: Remove completely from database

## 🏗️ Technical Architecture

### Core Components

#### 1. Session Manager (`app/core/session_manager.py`)
```python
class SessionManager:
    - store_session()      # Store Telegram session in MongoDB
    - get_user_sessions()  # Retrieve user's accounts
    - get_session_string() # Get session for specific phone
    - deactivate_session() # Logout account
    - delete_session()     # Remove account
    - can_add_account()    # Check account limits
```

#### 2. Telegram Login Manager (`app/core/telegram_login.py`)
```python
class TelegramLoginManager:
    - start_login_process()    # Send OTP via Pyrogram
    - verify_otp()            # Verify OTP and complete login
    - verify_2fa_password()   # Handle 2FA authentication
    - test_account_connection() # Validate stored sessions
    - cleanup_expired_sessions() # Remove expired login attempts
```

#### 3. Updated Main Bot (`app/bot/main.py`)
- **New Handlers**: Login, accounts, OTP, 2FA handlers
- **State Management**: Tracks user login progress
- **UI Integration**: Updated keyboards and menus
- **Error Handling**: Comprehensive error responses

### Database Schema

#### Accounts Collection
```javascript
{
  _id: ObjectId,
  owner_user_id: Number,     // Telegram user ID
  phone: String,             // Phone number (unique)
  session_string: String,    // Encrypted Pyrogram session
  account_name: String,      // Display name
  created_at: Date,          // When account was added
  last_used: Date,           // Last activity timestamp
  is_active: Boolean,        // Account status
  status: String             // "active" | "deactivated"
}
```

## 🚀 Setup Instructions

### 1. Environment Configuration
Ensure your `.env` file includes:
```env
API_ID=your_telegram_api_id
API_HASH=your_telegram_api_hash
BOT_TOKEN=your_bot_token
MONGO_URI=mongodb://localhost:27017
MONGO_DB=telegram_ads
MAX_ACCOUNTS_PER_USER=3
```

### 2. Dependencies
The system uses existing dependencies:
- `pyrogram` - Telegram client library
- `motor` - Async MongoDB driver
- `aiogram` - Bot framework
- `cryptography` - Session encryption

### 3. Database Setup
MongoDB collections are automatically created with proper indexes:
- `accounts` - User account sessions
- `users` - User states and configurations

### 4. Running the Bot
```bash
# Test the login system
python test_login.py

# Start the bot
python -m app.bot.main
```

## 🔧 Configuration Options

### Settings (`app/core/config.py`)
```python
MAX_ACCOUNTS_PER_USER = 3        # Maximum accounts per user
SESSION_ENCRYPTION_KEY = "..."   # Session encryption key
API_ID = 12345                   # Telegram API ID
API_HASH = "abcdef..."          # Telegram API Hash
```

### Customization
- **Account Limits**: Modify `MAX_ACCOUNTS_PER_USER`
- **Cleanup Interval**: Adjust cleanup task frequency (default: 5 minutes)
- **Session Timeout**: Modify login session expiry (default: 15 minutes)

## 🛠️ Usage Examples

### For Users
1. **Adding First Account**:
   - Tap "🔑 Login" → "🔑 Start Login"
   - Enter phone: `+1234567890`
   - Enter OTP with spaces: `1 2 3 4 5`
   - Enter 2FA password if required

2. **Managing Accounts**:
   - Tap "👥 My Accounts"
   - Select account to view details
   - Use "🔄 Test Connection" to verify
   - Use "🚪 Logout" to deactivate

### For Developers
```python
# Get user's accounts
accounts = await session_manager.get_user_sessions(user_id)

# Create Pyrogram client from stored session
client = await session_manager.create_client(user_id, phone)

# Test account connection
result = await telegram_login_manager.test_account_connection(user_id, phone)
```

## 🔍 Troubleshooting

### Common Issues

1. **"Invalid Phone Number"**
   - Ensure country code is included (+1, +91, etc.)
   - No spaces or special characters

2. **"Rate Limited"**
   - Wait for the specified time
   - Telegram enforces rate limits on login attempts

3. **"Invalid OTP"**
   - Enter code with spaces: `1 2 3 4 5`
   - Ensure all digits are correct

4. **"Connection Test Failed"**
   - Account may be logged out elsewhere
   - Session may have expired
   - Try logging in again

### Debug Mode
Enable debug logging in `main.py`:
```python
logging.basicConfig(level=logging.DEBUG)
```

## 🔒 Security Considerations

1. **Session Storage**: Sessions are encrypted before storage
2. **Rate Limiting**: Respects Telegram's API limits
3. **Automatic Cleanup**: Expired sessions are removed
4. **Access Control**: Users can only manage their own accounts
5. **Error Handling**: No sensitive data in error messages

## 🎯 Integration with Campaigns

The login system integrates seamlessly with the existing campaign system:

1. **Account Selection**: Campaigns can use any logged-in account
2. **Load Balancing**: Distribute messages across multiple accounts
3. **Fallback Handling**: Skip invalid accounts automatically
4. **Session Refresh**: Automatically handle session renewals

## 📈 Monitoring & Analytics

- **Account Status**: Track active/inactive accounts
- **Login Attempts**: Monitor successful/failed logins
- **Session Health**: Regular connection testing
- **Usage Statistics**: Track account usage in campaigns

---

## 🎉 Implementation Complete!

The Telegram login system is now fully implemented with:

✅ **Enterprise-grade security**  
✅ **Multi-account support**  
✅ **Persistent sessions**  
✅ **Comprehensive error handling**  
✅ **User-friendly interface**  
✅ **MongoDB integration**  
✅ **Automatic cleanup**  
✅ **2FA support**  

The system is ready for production use and will provide a robust foundation for your advertisement bot's account management needs.
