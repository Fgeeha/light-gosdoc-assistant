import logging
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from fastapi import FastAPI
from sqlalchemy import func, select

from app.config import get_settings
from app.db import SessionLocal, User, init_db
from app.routers import admin, auth, tools
from app.security import hash_password

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
logger = logging.getLogger("gosdoc")


def bootstrap_admin() -> None:
    s = get_settings()
    with SessionLocal() as db:
        if db.scalar(select(func.count()).select_from(User)):
            return
        if not (s.admin_username and s.admin_password):
            logger.warning("Пользователей нет: задайте ADMIN_USERNAME и ADMIN_PASSWORD")
            return
        db.add(
            User(
                username=s.admin_username,
                full_name="Администратор",
                password_hash=hash_password(s.admin_password),
                is_admin=True,
            )
        )
        db.commit()
        logger.info("Создан администратор %s", s.admin_username)


@asynccontextmanager
async def lifespan(_: FastAPI) -> AsyncIterator[None]:
    init_db()
    bootstrap_admin()
    yield


app = FastAPI(
    title="ГосДок Ассистент",
    lifespan=lifespan,
    docs_url="/api/docs",
    openapi_url="/api/openapi.json",
)
app.include_router(auth.router)
app.include_router(admin.router)
app.include_router(tools.router)


@app.get("/api/health")
def health() -> dict:
    return {"status": "ok"}
