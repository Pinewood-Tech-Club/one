"""
Encryption utilities for secure token storage
"""
import os
from cryptography.fernet import Fernet
from config import Config

# Initialize encryption
ENCRYPTION_KEY = Config.ENCRYPTION_KEY
if not ENCRYPTION_KEY:
    # Generate a new key if not provided (for development)
    ENCRYPTION_KEY = Fernet.generate_key()
    print(f"Generated new encryption key: {ENCRYPTION_KEY.decode()}")
    print("Add this to your .env file as ENCRYPTION_KEY for production!")
else:
    ENCRYPTION_KEY = ENCRYPTION_KEY.encode()

fernet = Fernet(ENCRYPTION_KEY)


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

