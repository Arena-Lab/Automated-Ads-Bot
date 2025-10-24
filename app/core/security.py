from __future__ import annotations

import base64
from cryptography.fernet import Fernet, InvalidToken
from .config import settings


def _get_fernet() -> Fernet:
    key_b64 = settings.SESSION_ENCRYPTION_KEY
    if len(key_b64) != 44:  # base64 of 32 bytes is 44 chars
        # allow raw 32 bytes in base64 without padding issues
        key = base64.urlsafe_b64encode(base64.b64decode(key_b64 + '==')) if key_b64 else Fernet.generate_key()
        return Fernet(key)
    return Fernet(key_b64.encode())


def encrypt(text: str) -> str:
    f = _get_fernet()
    return f.encrypt(text.encode()).decode()


def decrypt(token: str) -> str:
    f = _get_fernet()
    try:
        return f.decrypt(token.encode()).decode()
    except InvalidToken:
        raise ValueError("Invalid encryption token")
