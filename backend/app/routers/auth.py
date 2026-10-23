from fastapi import APIRouter, HTTPException, Response, status
from sqlalchemy import select

from app.db import User
from app.schemas import LoginIn, UserOut
from app.security import COOKIE_NAME, DB, CurrentUser, set_session_cookie, verify_password

router = APIRouter(prefix="/api/auth", tags=["auth"])


@router.post("/login", response_model=UserOut)
def login(body: LoginIn, response: Response, db: DB) -> User:
    user = db.scalar(select(User).where(User.username == body.username.strip()))
    if not verify_password(body.password, user.password_hash if user else None) or user is None:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Неверный логин или пароль")
    if not user.is_active:
        raise HTTPException(status.HTTP_403_FORBIDDEN, "Учётная запись заблокирована")
    set_session_cookie(response, user)
    return user


@router.post("/logout", status_code=status.HTTP_204_NO_CONTENT)
def logout(user: CurrentUser, response: Response, db: DB) -> None:
    user.token_version += 1
    db.commit()
    response.delete_cookie(COOKIE_NAME)


@router.get("/me", response_model=UserOut)
def me(user: CurrentUser) -> User:
    return user
