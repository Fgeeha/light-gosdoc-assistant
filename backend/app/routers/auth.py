import time

from fastapi import APIRouter, HTTPException, Request, Response, status
from sqlalchemy import func, select

from app.audit import audit, client_ip
from app.db import User
from app.schemas import LoginIn, UserOut
from app.security import COOKIE_NAME, DB, CurrentUser, set_session_cookie, verify_password

router = APIRouter(prefix="/api/auth", tags=["auth"])

# Защита учётной записи от перебора независимо от того, сколько адресов у атакующего.
# ponytail: счётчик в памяти процесса — сбрасывается при рестарте, у каждого воркера свой.
MAX_FAILURES = 5
LOCK_SECONDS = 15 * 60
_failures: dict[str, list[float]] = {}


def _recent_failures(name: str) -> list[float]:
    now = time.monotonic()
    recent = [t for t in _failures.pop(name, []) if now - t < LOCK_SECONDS]
    if recent:
        _failures[name] = recent
    return recent


def _record_failure(name: str) -> None:
    if len(_failures) > 10_000:  # перебор по случайным логинам не должен съесть память
        for key in list(_failures):
            _recent_failures(key)
    _failures.setdefault(name, []).append(time.monotonic())


@router.post("/login", response_model=UserOut)
def login(body: LoginIn, request: Request, response: Response, db: DB) -> User:
    login_name = body.username.strip().lower()
    ip = client_ip(request)
    # Ответ одинаков для существующих и несуществующих логинов — не раскрывает, кто есть в системе.
    if len(_recent_failures(login_name)) >= MAX_FAILURES:
        audit.warning("Вход заблокирован после неудачных попыток: %r, адрес %s", login_name, ip)
        raise HTTPException(
            status.HTTP_429_TOO_MANY_REQUESTS,
            "Слишком много неудачных попыток входа. Повторите через 15 минут",
        )
    user = db.scalar(select(User).where(func.lower(User.username) == login_name).limit(1))
    if not verify_password(body.password, user.password_hash if user else None) or user is None:
        _record_failure(login_name)
        audit.warning("Неудачный вход: %r, адрес %s", login_name, ip)
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Неверный логин или пароль")
    if not user.is_active:
        audit.warning("Вход в заблокированную учётную запись: %r, адрес %s", user.username, ip)
        raise HTTPException(status.HTTP_403_FORBIDDEN, "Учётная запись заблокирована")
    _failures.pop(login_name, None)
    audit.info("Вход: %r, адрес %s", user.username, ip)
    set_session_cookie(response, user)
    return user


@router.post("/logout", status_code=status.HTTP_204_NO_CONTENT)
def logout(user: CurrentUser, response: Response, db: DB) -> None:
    user.token_version += 1
    db.commit()
    audit.info("Выход: %r", user.username)
    response.delete_cookie(COOKIE_NAME)


@router.get("/me", response_model=UserOut)
def me(user: CurrentUser) -> User:
    return user
