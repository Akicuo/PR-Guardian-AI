"""Security utilities for JWT and encryption"""

from datetime import datetime, timedelta
from typing import Optional

from jose import JWTError, jwt
from passlib.context import CryptContext
from cryptography.fernet import Fernet

from app.config import get_settings

settings = get_settings()

# Password hashing (for future use if we add passwords)
pwd_context = CryptContext(schemes=["bcrypt"], deprecated="auto")

# Encryption for sensitive data (GitHub tokens)
# Using the secret_key as the encryption key
_cipher_suite = None


def get_cipher_suite() -> Fernet:
    """
    Get or create Fernet cipher suite for encryption.
    The secret_key must be 32 url-safe base64-encoded bytes.
    """
    global _cipher_suite
    if _cipher_suite is None:
        # Ensure secret_key is 32 bytes for Fernet
        secret = settings.secret_key.encode()
        if len(secret) < 32:
            secret = secret.ljust(32, b'=')
        elif len(secret) > 32:
            import hashlib
            secret = hashlib.sha256(secret).digest()

        from base64 import urlsafe_b64encode
        key = urlsafe_b64encode(secret)
        _cipher_suite = Fernet(key)
    return _cipher_suite


def encrypt_token(token: str) -> str:
    """
    Encrypt a sensitive token (e.g., GitHub OAuth token).

    Args:
        token: Plain text token

    Returns:
        Encrypted token as string
    """
    cipher = get_cipher_suite()
    return cipher.encrypt(token.encode()).decode()


def decrypt_token(encrypted_token: str) -> str:
    """
    Decrypt an encrypted token.

    Args:
        encrypted_token: Encrypted token string

    Returns:
        Decrypted plain text token
    """
    cipher = get_cipher_suite()
    return cipher.decrypt(encrypted_token.encode()).decode()


def create_access_token(data: dict, expires_delta: Optional[timedelta] = None) -> str:
    """
    Create a JWT access token.

    Args:
        data: Data to encode in the token (e.g., {"user_id": 123})
        expires_delta: Optional expiration time delta

    Returns:
        Encoded JWT token
    """
    to_encode = data.copy()

    if expires_delta:
        expire = datetime.utcnow() + expires_delta
    else:
        expire = datetime.utcnow() + timedelta(days=7)

    to_encode.update({"exp": expire})
    encoded_jwt = jwt.encode(to_encode, settings.secret_key, algorithm=settings.jwt_algorithm)
    return encoded_jwt


def decode_access_token(token: str) -> Optional[dict]:
    """
    Decode and verify a JWT access token.

    Args:
        token: JWT token to decode

    Returns:
        Decoded token data, or None if invalid
    """
    try:
        payload = jwt.decode(token, settings.secret_key, algorithms=[settings.jwt_algorithm])
        return payload
    except JWTError:
        return None
