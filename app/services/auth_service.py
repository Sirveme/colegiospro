# ══════════════════════════════════════════════════════════
# app/services/auth_service.py
# JWT en cookie httponly + hash de contraseña (Argon2 si existe,
# fallback a PBKDF2 con hashlib para no romper si falta el paquete)
# ══════════════════════════════════════════════════════════

import os
import hmac
import json
import base64
import hashlib
import secrets
from datetime import datetime, timezone, timedelta
from typing import Optional

from fastapi import Request

# ─── Password hashing ───
try:
    from argon2 import PasswordHasher
    from argon2.exceptions import VerifyMismatchError
    _ph = PasswordHasher()
    _USE_ARGON2 = True
except Exception:
    _ph = None
    _USE_ARGON2 = False


def hash_password(plain: str) -> str:
    if _USE_ARGON2:
        return _ph.hash(plain)
    # Fallback: PBKDF2-HMAC-SHA256
    salt = secrets.token_bytes(16)
    dk = hashlib.pbkdf2_hmac("sha256", plain.encode("utf-8"), salt, 200_000)
    return "pbkdf2$" + base64.b64encode(salt).decode() + "$" + base64.b64encode(dk).decode()


def verify_password(plain: str, hashed: str) -> bool:
    if hashed.startswith("pbkdf2$"):
        try:
            _, salt_b64, dk_b64 = hashed.split("$", 2)
            salt = base64.b64decode(salt_b64)
            expected = base64.b64decode(dk_b64)
            dk = hashlib.pbkdf2_hmac("sha256", plain.encode("utf-8"), salt, 200_000)
            return hmac.compare_digest(dk, expected)
        except Exception:
            return False
    if _USE_ARGON2:
        try:
            return _ph.verify(hashed, plain)
        except VerifyMismatchError:
            return False
        except Exception:
            return False
    return False


# ─── JWT minimal (HS256) sin dependencias externas ───
JWT_SECRET = os.environ.get("SECRETARIA_JWT_SECRET") or os.environ.get("SECRET_KEY") or "dev-secret-change-me"
JWT_ALGO = "HS256"
JWT_TTL_HOURS = 24 * 7  # 7 días
COOKIE_NAME = "secretaria_session"

# secure=True solo cuando hay HTTPS real (Railway / producción).
# En dev/test (sqlite local) usamos cookies normales para no romper TestClient.
_DB_URL = os.environ.get("DATABASE_URL", "")
COOKIE_SECURE = bool(_DB_URL) and not _DB_URL.startswith("sqlite")


def _b64url_encode(raw: bytes) -> str:
    return base64.urlsafe_b64encode(raw).rstrip(b"=").decode("ascii")


def _b64url_decode(s: str) -> bytes:
    pad = "=" * (-len(s) % 4)
    return base64.urlsafe_b64decode(s + pad)


def create_jwt(payload: dict, ttl_hours: int = JWT_TTL_HOURS) -> str:
    header = {"alg": "HS256", "typ": "JWT"}
    body = dict(payload)
    body["exp"] = int((datetime.now(timezone.utc) + timedelta(hours=ttl_hours)).timestamp())
    body["iat"] = int(datetime.now(timezone.utc).timestamp())
    h = _b64url_encode(json.dumps(header, separators=(",", ":")).encode())
    p = _b64url_encode(json.dumps(body, separators=(",", ":")).encode())
    sig = hmac.new(JWT_SECRET.encode(), f"{h}.{p}".encode(), hashlib.sha256).digest()
    return f"{h}.{p}.{_b64url_encode(sig)}"


def decode_jwt(token: str) -> Optional[dict]:
    try:
        h, p, s = token.split(".")
        expected = hmac.new(JWT_SECRET.encode(), f"{h}.{p}".encode(), hashlib.sha256).digest()
        if not hmac.compare_digest(_b64url_encode(expected), s):
            return None
        body = json.loads(_b64url_decode(p))
        if int(body.get("exp", 0)) < int(datetime.now(timezone.utc).timestamp()):
            return None
        return body
    except Exception:
        return None


def get_current_user_id(request: Request) -> Optional[int]:
    token = request.cookies.get(COOKIE_NAME)
    if not token:
        return None
    data = decode_jwt(token)
    if not data:
        return None
    uid = data.get("uid")
    return int(uid) if uid is not None else None


def set_session_cookie(response, user_id: int, nombre: str = ""):
    token = create_jwt({"uid": user_id, "nom": nombre})
    response.set_cookie(
        key=COOKIE_NAME,
        value=token,
        httponly=True,
        secure=COOKIE_SECURE,
        samesite="lax",
        max_age=JWT_TTL_HOURS * 3600,
        path="/",
    )


def clear_session_cookie(response):
    response.delete_cookie(COOKIE_NAME, path="/")


def generar_token_verificacion() -> str:
    return secrets.token_urlsafe(32)
