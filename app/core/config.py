from __future__ import annotations

from pydantic_settings import BaseSettings, SettingsConfigDict
from pydantic import field_validator
from typing import List
import os
from dotenv import load_dotenv, dotenv_values

# Compute project root based on this file location
PROJECT_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
ENV_PATH = os.path.join(PROJECT_ROOT, ".env")


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=ENV_PATH, env_file_encoding="utf-8", case_sensitive=False)

    # Telegram bot and API
    BOT_TOKEN: str
    API_ID: int
    API_HASH: str

    # Database
    MONGO_URI: str
    MONGO_DB: str = "telegram_ads"

    # Redis / Queue
    REDIS_URL: str

    # Admins
    ADMIN_IDS: List[int] = []

    # Force subscription
    FORCE_SUB_CHATS: List[str] = []

    # Limits and presets
    MAX_ACCOUNTS_PER_USER: int = 3
    INTERVAL_PRESETS_SAFE: int = 2
    INTERVAL_PRESETS_DEFAULT: int = 5
    INTERVAL_PRESETS_AGGRESSIVE: int = 10

    ALLOW_PRIVATE_TARGETS: bool = True
    ALLOW_PUBLIC_TARGETS: bool = True

    # Sessions
    SESSION_ENCRYPTION_KEY: str

    # Branding / Policy
    BOT_DISPLAY_NAME: str = "Ads Assistant"
    START_MEDIA_URL: str | None = None
    POLICY_TEXT: str = "Use respectfully."

    # Profile modifications
    ACCOUNT_NAME_SUFFIX: str = " | Automated Ads via @YourBot"
    ACCOUNT_BIO_TEMPLATE: str = "Messages are automated by @YourBot - Free"

    # Targets
    TARGETS_MAX_EXCLUDE: int = 100
    TARGETS_MAX_INCLUDE: int = 100

    @field_validator("ADMIN_IDS", mode="before")
    @classmethod
    def parse_admin_ids(cls, v):
        if isinstance(v, list):
            return [int(x) for x in v]
        if not v:
            return []
        return [int(x.strip()) for x in str(v).split(",") if x.strip()]

    @field_validator("FORCE_SUB_CHATS", mode="before")
    @classmethod
    def parse_force_sub_chats(cls, v):
        if isinstance(v, list):
            return v
        if not v:
            return []
        return [x.strip() for x in str(v).split(",") if x.strip()]


# Ensure .env is loaded from project root regardless of CWD
load_dotenv(dotenv_path=ENV_PATH)
# Force set values into os.environ in case loader didn't export them
for k, v in (dotenv_values(ENV_PATH) or {}).items():
    if k and v is not None and k not in os.environ:
        os.environ[k] = v
settings = Settings()

# convenience
PROJECT_ROOT = PROJECT_ROOT
