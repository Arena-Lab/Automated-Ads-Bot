from __future__ import annotations

from aiogram.types import InlineKeyboardMarkup, InlineKeyboardButton
from app.core.config import settings


def main_menu_kb(campaign_running: bool = False, is_admin: bool = False) -> InlineKeyboardMarkup:
    rows = [
        # Top row: Login and My Accounts buttons (2x2 format)
        [InlineKeyboardButton(text="🔑 Login", callback_data="menu:login"),
         InlineKeyboardButton(text="👥 My Accounts", callback_data="menu:accounts")],
        # Original buttons
        [InlineKeyboardButton(text="📝 Set Message", callback_data="menu:set_msg"),
         InlineKeyboardButton(text="👁️ View Message", callback_data="menu:view_msg")],
        [InlineKeyboardButton(text="🎯 Targets", callback_data="menu:targets"),
         InlineKeyboardButton(text="📊 Analytics", callback_data="menu:analytics")],
        [
            InlineKeyboardButton(text="⏱️ Interval", callback_data="menu:interval"),
            InlineKeyboardButton(text="⏹️ Stop Campaign", callback_data="menu:stop") if campaign_running 
            else InlineKeyboardButton(text="▶️ Start Ads", callback_data="menu:start"),
        ],
        [
            InlineKeyboardButton(text="🤖 Auto Reply", callback_data="menu:autoreply"),
        ],
        [
            InlineKeyboardButton(text="📜 Policy", callback_data="menu:policy"),
        ],
    ]
    if is_admin:
        rows.append([InlineKeyboardButton(text="🛠️ Admin", callback_data="menu:admin")])
    return InlineKeyboardMarkup(inline_keyboard=rows)


def admin_menu_kb() -> InlineKeyboardMarkup:
    rows = [
        [InlineKeyboardButton(text="⚙️ Diagnostics", callback_data="admin:diagnostics")],
        [InlineKeyboardButton(text="🔁 Restart VPS", callback_data="admin:restart")],
        [InlineKeyboardButton(text="↩️ Back", callback_data="menu:home")],
    ]
    return InlineKeyboardMarkup(inline_keyboard=rows)


def confirm_restart_kb() -> InlineKeyboardMarkup:
    rows = [
        [InlineKeyboardButton(text="✅ Confirm Restart", callback_data="admin:restart:confirm")],
        [InlineKeyboardButton(text="❌ Cancel", callback_data="admin:restart:cancel")],
    ]
    return InlineKeyboardMarkup(inline_keyboard=rows)


def analytics_kb() -> InlineKeyboardMarkup:
    rows = [
        [InlineKeyboardButton(text="🎯 Show Targets", callback_data="analytics:targets"),
         InlineKeyboardButton(text="🔄 Refresh", callback_data="analytics:refresh")],
        [InlineKeyboardButton(text="↩️ Back", callback_data="menu:home")],
    ]
    return InlineKeyboardMarkup(inline_keyboard=rows)


def back_to_menu_kb() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[[InlineKeyboardButton(text="↩️ Back", callback_data="menu:home")]])


def otp_keypad_kb() -> InlineKeyboardMarkup:
    # Login removed: retain a minimal back button to avoid import breaks if referenced
    return InlineKeyboardMarkup(inline_keyboard=[[InlineKeyboardButton(text="↩️ Back", callback_data="menu:home")]])


def targets_menu_kb(types: dict | None = None) -> InlineKeyboardMarkup:
    rows = [
        [InlineKeyboardButton(text="📥 Only These IDs", callback_data="targets:include")],
        [InlineKeyboardButton(text="🌐 All Dialogs", callback_data="targets:all")],
        [InlineKeyboardButton(text="🚫 Exclude IDs", callback_data="targets:exclude")],
    ]

    if types is not None:
        def onoff(v: bool) -> str:
            return "✅" if v else "❌"
        rows.extend([
            [
                InlineKeyboardButton(text=f"{onoff(types.get('private', True))} Personal", callback_data="targets:type:private"),
                InlineKeyboardButton(text=f"{onoff(types.get('group', True))} Groups", callback_data="targets:type:group"),
            ],
            [
                InlineKeyboardButton(text=f"{onoff(types.get('supergroup', True))} Supergroups", callback_data="targets:type:supergroup"),
                InlineKeyboardButton(text=f"{onoff(types.get('channel', True))} Channels", callback_data="targets:type:channel"),
            ],
        ])

    rows.append([InlineKeyboardButton(text="↩️ Back", callback_data="menu:home")])
    return InlineKeyboardMarkup(inline_keyboard=rows)


def interval_menu_kb(cycle_enabled: bool = False, rest_seconds: int | None = None) -> InlineKeyboardMarkup:
    rows = [
        [InlineKeyboardButton(text=f"🛡️ Safe ({settings.INTERVAL_PRESETS_SAFE}/min)", callback_data="interval:safe")],
        [InlineKeyboardButton(text=f"⚖️ Default ({settings.INTERVAL_PRESETS_DEFAULT}/min)", callback_data="interval:default")],
        [InlineKeyboardButton(text=f"🔥 Aggressive ({settings.INTERVAL_PRESETS_AGGRESSIVE}/min)", callback_data="interval:aggressive")],
        [InlineKeyboardButton(text="✍️ Custom", callback_data="interval:custom")],
        [InlineKeyboardButton(text=f"♻️ Auto Cycle: {'ON' if cycle_enabled else 'OFF'}", callback_data="interval:cycle_toggle")],
        [
            InlineKeyboardButton(text="⏱️ 30s", callback_data="interval:rest:30"),
            InlineKeyboardButton(text="⏱️ 60s", callback_data="interval:rest:60"),
            InlineKeyboardButton(text="⏱️ 300s", callback_data="interval:rest:300"),
        ],
        [InlineKeyboardButton(text="✍️ Custom Rest (s)", callback_data="interval:rest_custom")],
        [InlineKeyboardButton(text="↩️ Back", callback_data="menu:home")],
    ]
    return InlineKeyboardMarkup(inline_keyboard=rows)


def login_menu_kb() -> InlineKeyboardMarkup:
    """Keyboard for login process"""
    rows = [
        [InlineKeyboardButton(text="🔑 Start Login", callback_data="login:start")],
        [InlineKeyboardButton(text="❓ How to Login", callback_data="login:help")],
        [InlineKeyboardButton(text="↩️ Back", callback_data="menu:home")],
    ]
    return InlineKeyboardMarkup(inline_keyboard=rows)


def accounts_menu_kb(accounts: list = None) -> InlineKeyboardMarkup:
    """Keyboard for accounts management"""
    rows = []
    
    if accounts:
        for i, account in enumerate(accounts[:5]):  # Show max 5 accounts
            phone = account.get("phone", "Unknown")
            status = account.get("status", "active")
            status_emoji = "✅" if status == "active" else "❌"
            rows.append([InlineKeyboardButton(
                text=f"{status_emoji} {phone}",
                callback_data=f"account:view:{account['id']}"
            )])
    
    # Add management buttons
    rows.extend([
        [InlineKeyboardButton(text="➕ Add Account", callback_data="login:start")],
        [InlineKeyboardButton(text="🔄 Refresh", callback_data="menu:accounts")],
        [InlineKeyboardButton(text="↩️ Back", callback_data="menu:home")],
    ])
    
    return InlineKeyboardMarkup(inline_keyboard=rows)


def account_detail_kb(account_id: str) -> InlineKeyboardMarkup:
    """Keyboard for individual account details"""
    rows = [
        [InlineKeyboardButton(text="🔄 Test Connection", callback_data=f"account:test:{account_id}")],
        [InlineKeyboardButton(text="🚪 Logout Account", callback_data=f"account:logout:{account_id}")],
        [InlineKeyboardButton(text="🗑️ Delete Account", callback_data=f"account:delete:{account_id}")],
        [InlineKeyboardButton(text="↩️ Back to Accounts", callback_data="menu:accounts")],
    ]
    return InlineKeyboardMarkup(inline_keyboard=rows)
