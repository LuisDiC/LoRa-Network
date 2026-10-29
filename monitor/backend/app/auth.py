"""
Utilidades de autenticación JWT y hashing de contraseñas.
Provee:
  - hash_password()       — bcrypt nativo con truncamiento seguro a 72 bytes
  - verify_password()     — verificación nativa de bcrypt en tiempo constante
  - create_access_token() — genera JWT firmado con settings.jwt_secret_key
  - decode_access_token() — decodifica y valida JWT; retorna None si inválido
"""
from datetime import datetime, timedelta, timezone
from typing import Optional

import bcrypt
from jose import JWTError, jwt

from app.config import settings


# ---------------------------------------------------------------------------
# Hashing de contraseñas con bcrypt nativo
# ---------------------------------------------------------------------------

def hash_password(plain: str) -> str:
    """
    Genera el hash bcrypt de la contraseña en texto plano.
    Bcrypt limita internamente la entrada a 72 bytes; se trunca de forma segura a 72 bytes UTF-8.
    """
    pwd_bytes = plain.encode("utf-8")[:72]
    salt = bcrypt.gensalt(rounds=12)
    return bcrypt.hashpw(pwd_bytes, salt).decode("utf-8")


def verify_password(plain: str, hashed: str) -> bool:
    """
    Compara una contraseña en texto plano con su hash bcrypt.
    Usa bcrypt.checkpw para comparación segura en tiempo constante.
    Retorna True si coinciden, False en cualquier otro caso (o si el hash es inválido).
    """
    try:
        pwd_bytes = plain.encode("utf-8")[:72]
        hashed_bytes = hashed.encode("utf-8")
        return bcrypt.checkpw(pwd_bytes, hashed_bytes)
    except Exception:
        return False


# ---------------------------------------------------------------------------
# JWT — creación y decodificación
# ---------------------------------------------------------------------------

def create_access_token(
    data: dict,
    expires_delta: Optional[timedelta] = None,
) -> str:
    """
    Genera un JWT firmado con la clave y algoritmo configurados en settings.

    Args:
        data: Payload a incluir en el token (por convención debe incluir 'sub').
        expires_delta: Tiempo de vida del token. Si es None, usa
                       jwt_access_token_expire_minutes de settings.

    Returns:
        JWT codificado como string.
    """
    to_encode = data.copy()
    if expires_delta is not None:
        expire = datetime.now(timezone.utc) + expires_delta
    else:
        expire = datetime.now(timezone.utc) + timedelta(
            minutes=settings.jwt_access_token_expire_minutes
        )
    to_encode["exp"] = expire

    return jwt.encode(
        to_encode,
        settings.jwt_secret_key,
        algorithm=settings.jwt_algorithm,
    )


def decode_access_token(token: str) -> Optional[dict]:
    """
    Decodifica y valida un JWT.

    Verifica firma, algoritmo y expiración.
    Retorna el payload (dict) si el token es válido.
    Retorna None si el token está expirado, malformado o con firma inválida.
    """
    try:
        payload = jwt.decode(
            token,
            settings.jwt_secret_key,
            algorithms=[settings.jwt_algorithm],
        )
        return payload
    except JWTError:
        return None
