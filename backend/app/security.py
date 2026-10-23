"""Пароли, сессионные токены и зависимости авторизации."""

from datetime import UTC, datetime, timedelta
from typing import Annotated

import bcrypt
import jwt
from fastapi import Cookie, Depends, HTTPException, Response, status
from sqlalchemy.orm import Session

from app.config import get_settings
from app.db import User, get_db

COOKIE_NAME = "gosdoc_session"
_ALGORITHM = "HS256"


# bcrypt учитывает не более 72 байт; кириллица — 2 байта на символ.
MAX_PASSWORD_BYTES = 72
# Сравнение с фиктивным хешем, чтобы время ответа не выдавало существование логина.
_DUMMY_HASH = bcrypt.hashpw(b"dummy-password", bcrypt.gensalt()).decode()


def hash_password(password: str) -> str:
    if len(password.encode()) > MAX_PASSWORD_BYTES:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, "Пароль длиннее 72 байт")
    return bcrypt.hashpw(password.encode(), bcrypt.gensalt()).decode()


def verify_password(password: str, password_hash: str | None) -> bool:
    data = password.encode()
    if len(data) > MAX_PASSWORD_BYTES:
        return False
    return bcrypt.checkpw(data, (password_hash or _DUMMY_HASH).encode()) and bool(password_hash)


def set_session_cookie(response: Response, user: User) -> None:
    settings = get_settings()
    ttl = timedelta(minutes=settings.session_ttl_minutes)
    token = jwt.encode(
        {"sub": str(user.id), "ver": user.token_version, "exp": datetime.now(UTC) + ttl},
        settings.secret_key,
        algorithm=_ALGORITHM,
    )
    response.set_cookie(
        COOKIE_NAME,
        token,
        max_age=int(ttl.total_seconds()),
        httponly=True,
        secure=settings.cookie_secure,
        samesite="strict",
    )


def current_user(
    db: Annotated[Session, Depends(get_db)],
    token: Annotated[str | None, Cookie(alias=COOKIE_NAME)] = None,
) -> User:
    unauthorized = HTTPException(status.HTTP_401_UNAUTHORIZED, "Требуется вход в систему")
    if not token:
        raise unauthorized
    try:
        payload = jwt.decode(token, get_settings().secret_key, algorithms=[_ALGORITHM])
    except jwt.PyJWTError:
        raise unauthorized from None
    user = db.get(User, int(payload["sub"]))
    if user is None or not user.is_active or payload.get("ver") != user.token_version:
        raise unauthorized
    return user


def admin_user(user: Annotated[User, Depends(current_user)]) -> User:
    if not user.is_admin:
        raise HTTPException(status.HTTP_403_FORBIDDEN, "Недостаточно прав")
    return user


CurrentUser = Annotated[User, Depends(current_user)]
AdminUser = Annotated[User, Depends(admin_user)]
DB = Annotated[Session, Depends(get_db)]
