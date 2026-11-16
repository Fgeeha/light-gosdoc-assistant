import threading
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
MAX_TRACKED = 10_000  # перебор по случайным логинам не должен съесть память
# Логин → время попыток без успешного входа. Порядок вставки = давность последней попытки.
_failures: dict[str, list[float]] = {}
_lock = threading.Lock()  # вход — синхронный обработчик, вызовы идут из пула потоков


def _reserve_attempt(name: str) -> bool:
    """Учесть попытку входа заранее; False — лимит исчерпан.

    Проверка и учёт под одной блокировкой: иначе параллельные запросы проходят проверку раньше,
    чем записан хоть один отказ. Успешный вход стирает счётчик (_forget).
    """
    now = time.monotonic()
    with _lock:
        recent = [t for t in _failures.pop(name, []) if now - t < LOCK_SECONDS]
        if len(recent) >= MAX_FAILURES:
            _failures[name] = recent
            return False
        _failures[name] = [*recent, now]
        while len(_failures) > MAX_TRACKED:
            _failures.pop(next(iter(_failures)))  # вытесняем логин, давно не встречавшийся
        return True


def _forget(name: str) -> None:
    with _lock:
        _failures.pop(name, None)


@router.post("/login", response_model=UserOut)
def login(body: LoginIn, request: Request, response: Response, db: DB) -> User:
    login_name = body.username.strip().lower()
    ip = client_ip(request)
    # Ответ одинаков для существующих и несуществующих логинов — не раскрывает, кто есть в системе.
    if not _reserve_attempt(login_name):
        audit.warning("Вход заблокирован после неудачных попыток: %r, адрес %s", login_name, ip)
        raise HTTPException(
            status.HTTP_429_TOO_MANY_REQUESTS,
            "Слишком много неудачных попыток входа. Повторите через 15 минут",
        )
    # В старой БД могут быть «Admin» и «admin»: точное совпадение имеет приоритет.
    user = db.scalar(
        select(User)
        .where(func.lower(User.username) == login_name)
        .order_by((User.username == body.username.strip()).desc(), User.id)
        .limit(1)
    )
    if not verify_password(body.password, user.password_hash if user else None) or user is None:
        audit.warning("Неудачный вход: %r, адрес %s", login_name, ip)
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Неверный логин или пароль")
    if not user.is_active:
        audit.warning("Вход в заблокированную учётную запись: %r, адрес %s", user.username, ip)
        raise HTTPException(status.HTTP_403_FORBIDDEN, "Учётная запись заблокирована")
    _forget(login_name)
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
