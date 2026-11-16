from fastapi import APIRouter, HTTPException, Response, status
from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError

from app.audit import audit
from app.db import User
from app.schemas import UserCreate, UserOut, UserUpdate
from app.security import DB, AdminUser, hash_password, set_session_cookie

router = APIRouter(prefix="/api/admin/users", tags=["admin"])


def _get(db: DB, user_id: int) -> User:
    user = db.get(User, user_id)
    if user is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Пользователь не найден")
    return user


@router.get("", response_model=list[UserOut])
def list_users(_: AdminUser, db: DB) -> list[User]:
    return list(db.scalars(select(User).order_by(User.created_at)))


@router.post("", response_model=UserOut, status_code=status.HTTP_201_CREATED)
def create_user(body: UserCreate, admin: AdminUser, db: DB) -> User:
    # Логины сравниваются без учёта регистра: «Ivanov» и «ivanov» — один человек.
    taken = HTTPException(status.HTTP_409_CONFLICT, "Логин уже занят")
    if db.scalar(select(User).where(func.lower(User.username) == body.username.lower())):
        raise taken
    user = User(
        username=body.username,
        full_name=body.full_name.strip(),
        password_hash=hash_password(body.password),
        is_admin=body.is_admin,
    )
    db.add(user)
    try:
        db.commit()
    except IntegrityError:  # тот же логин создан параллельным запросом
        db.rollback()
        raise taken from None
    audit.info(
        "%r создал пользователя %r (администратор: %s)",
        admin.username,
        user.username,
        user.is_admin,
    )
    return user


@router.patch("/{user_id}", response_model=UserOut)
def update_user(
    user_id: int, body: UserUpdate, admin: AdminUser, db: DB, response: Response
) -> User:
    user = _get(db, user_id)
    if user.id == admin.id and (body.is_admin is False or body.is_active is False):
        raise HTTPException(
            status.HTTP_400_BAD_REQUEST, "Нельзя снять права или заблокировать себя"
        )
    if body.full_name is not None:
        user.full_name = body.full_name.strip()
    if body.password is not None:
        user.password_hash = hash_password(body.password)
        user.token_version += 1  # завершает все сессии пользователя
    if body.is_admin is not None:
        user.is_admin = body.is_admin
    if body.is_active is not None:
        user.is_active = body.is_active
    db.commit()
    changed = sorted(body.model_dump(exclude_unset=True, exclude={"password"}))
    audit.info(
        "%r изменил пользователя %r: %s%s",
        admin.username,
        user.username,
        ", ".join(changed) or "—",
        ", пароль" if body.password is not None else "",
    )
    if body.password is not None and user.id == admin.id:
        set_session_cookie(response, user)  # иначе администратор выкинет из системы сам себя
    return user


@router.delete("/{user_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_user(user_id: int, admin: AdminUser, db: DB) -> None:
    user = _get(db, user_id)
    if user.id == admin.id:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "Нельзя удалить себя")
    db.delete(user)
    db.commit()
    audit.info("%r удалил пользователя %r", admin.username, user.username)
