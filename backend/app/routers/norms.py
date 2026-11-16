"""Справочник нормативных актов для администратора.

Просмотр, правка статуса записи (PATCH, хранится в БД — см. services/norms_store) и поиск
опубликованных изменений по pravo.gov.ru, чтобы было видно, какие акты пора проверить.
"""

import time
from datetime import date, timedelta
from typing import Literal

import httpx
from fastapi import APIRouter, HTTPException, Query, status
from pydantic import BaseModel

from app.db import Norm
from app.security import DB, AdminUser
from app.services import norms_store, sources
from app.services.normcheck import load_norms

router = APIRouter(prefix="/api/admin/norms", tags=["admin"])
UPDATES_MAX_SECONDS = 60


class NormOut(BaseModel):
    id: str
    title: str
    status: str
    since: str | None = None
    replaced_by: str | None = None
    facts_count: int
    verified: bool


def _to_out(n: dict) -> NormOut:
    return NormOut(
        id=n["id"],
        title=n["title"],
        status=n["status"],
        since=n.get("since"),
        replaced_by=n.get("replaced_by"),
        facts_count=len(n.get("facts") or []),
        # Записи без явного флага считаются проверенными (исходные акты справочника).
        verified=n.get("verified", True),
    )


class NormPatch(BaseModel):
    status: Literal["active", "repealed", "partially"] | None = None
    since: str | None = None
    replaced_by: str | None = None
    facts: list[str] | None = None
    # Отметка «проверено юристом»: правка из UI подтверждает запись.
    verified: bool = True


class Update(BaseModel):
    norm_id: str
    date: str
    name: str
    url: str


@router.get("", response_model=list[NormOut])
def list_norms(_: AdminUser) -> list[NormOut]:
    return [_to_out(n) for n in load_norms()]


@router.patch("/{norm_id}", response_model=NormOut)
def update_norm(norm_id: str, body: NormPatch, _: AdminUser, db: DB) -> NormOut:
    row = db.get(Norm, norm_id)
    if row is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Акт не найден в справочнике")
    data = dict(row.data)
    changes = body.model_dump(exclude_unset=True)
    if changes.get("replaced_by") and changes["replaced_by"] not in {
        n["title"] for n in load_norms()
    }:
        # По title подтягиваются нормы нового акта (normcheck.find_references).
        raise HTTPException(
            status.HTTP_422_UNPROCESSABLE_ENTITY,
            "Замена должна дословно совпадать с наименованием акта из справочника",
        )
    for key in ("status", "since", "replaced_by", "facts"):
        if key in changes:
            if changes[key] in (None, [], ""):
                data.pop(key, None)
            else:
                data[key] = changes[key]
    # Любая правка из UI — это подтверждение записи, если прямо не сказано иное.
    data["verified"] = body.verified
    row.data = data
    db.commit()
    norms_store.refresh()
    return _to_out(data)


@router.get("/updates", response_model=list[Update])
def check_updates(_: AdminUser, days: int = Query(30, ge=1, le=365)) -> list[sources.Change]:
    """Публикации за последние `days` дней, затрагивающие отслеживаемые акты (pravo.gov.ru)."""
    since = date.today() - timedelta(days)
    # Законы и постановления; агентские акты (federal_authorities) слишком объёмны для онлайн-кнопки.
    blocks = ("president", "government")
    # Обход — сотни запросов подряд; без общего предела кнопка могла висеть минутами.
    deadline = time.monotonic() + UPDATES_MAX_SECONDS
    try:
        with httpx.Client(base_url=sources.PRAVO_BASE_URL, timeout=30) as client:
            return sources.fetch_pravo_changes(client, since, blocks, deadline=deadline)
    except TimeoutError as e:
        raise HTTPException(
            status.HTTP_504_GATEWAY_TIMEOUT,
            "Портал опубликования отвечает слишком долго — сократите период проверки",
        ) from e
    except (KeyError, ValueError) as e:
        # Портал сменил формат ответа (нет полей, другая дата).
        raise HTTPException(
            status.HTTP_502_BAD_GATEWAY, "Портал опубликования вернул неожиданный ответ"
        ) from e
    except httpx.HTTPError as e:
        raise HTTPException(
            status.HTTP_502_BAD_GATEWAY, f"Портал опубликования недоступен: {e}"
        ) from e
