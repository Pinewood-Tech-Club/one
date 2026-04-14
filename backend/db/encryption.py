"""
Encryption utilities for secure token storage
"""
from cryptography.fernet import Fernet
from config import Config

ENCRYPTION_KEY = Config.ENCRYPTION_KEY
if not ENCRYPTION_KEY:
    raise RuntimeError("ENCRYPTION_KEY is not configured")

ENCRYPTION_KEY_BYTES = ENCRYPTION_KEY.encode()

fernet = Fernet(ENCRYPTION_KEY_BYTES)


def encrypt_token(token):
    """Encrypt a token for secure storage"""
    if not token:
        return None
    return fernet.encrypt(token.encode()).decode()


def decrypt_token(encrypted_token):
    """Decrypt a token for use"""
    if not encrypted_token:
        return None
    try:
        return fernet.decrypt(encrypted_token.encode()).decode()
    except Exception as e:
        print(f"Error decrypting token: {e}")
        return None
