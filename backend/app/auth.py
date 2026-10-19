"""
Utilidades de autenticación JWT y hashing de contraseñas.
Provee:
  - hash_password()       — bcrypt via passlib
  - verify_password()     — verificación constante-time de bcrypt
  - create_access_token() — genera JWT firmado con settings.jwt_secret_key
  - decode_access_token() — decodifica y valida JWT; retorna None si inválido
"""
from datetime import datetime, timedelta, timezone
from typing import Optional

from jose import JWTError, jwt
from passlib.context import CryptContext

from app.config import settings

# ---------------------------------------------------------------------------
# Contexto de hashing — bcrypt con auto-deprecation de esquemas débiles
# ---------------------------------------------------------------------------
_pwd_context = CryptContext(schemes=["bcrypt"], deprecated="auto")


# ---------------------------------------------------------------------------
# Hashing de contraseñas
# ---------------------------------------------------------------------------

def hash_password(plain: str) -> str:
    """
    Genera el hash bcrypt de la contraseña en texto plano.
    El hash incluye el salt internamente (trabajo factor 12 por defecto en passlib).
    """
    return _pwd_context.hash(plain)


def verify_password(plain: str, hashed: str) -> bool:
    """
    Compara una contraseña en texto plano con su hash bcrypt.
    Usa comparación en tiempo constante para mitigar timing attacks.
    Retorna True si coinciden, False en cualquier otro caso.
    """
    return _pwd_context.verify(plain, hashed)


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
