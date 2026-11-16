"""Справочник нормативных актов для администратора.

Просмотр, правка записи (PATCH, хранится в БД — см. services/norms_store) с журналом правок,
выгрузка в формате norms.json и поиск опубликованных изменений по pravo.gov.ru, чтобы было
видно, какие акты пора проверить.
"""

import time
from datetime import UTC, date, datetime, timedelta
from typing import Literal

import httpx
from fastapi import APIRouter, HTTPException, Query, status
from fastapi.responses import JSONResponse
from pydantic import BaseModel
from sqlalchemy import select

from app.db import Norm, NormChange
from app.security import DB, AdminUser
from app.services import norms_store, sources
from app.services.normcheck import load_norms, replacements

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
    updated_by: str | None = None
    updated_at: str | None = None


class NormChangeOut(BaseModel):
    changed_by: str
    changed_at: datetime
    before: dict
    after: dict


def _to_out(n: dict) -> NormOut:
    return NormOut(
        id=n["id"],
        title=n["title"],
        status=n["status"],
        since=n.get("since"),
        replaced_by="; ".join(replacements(n)) or None,
        facts_count=len(n.get("facts") or []),
        # Записи без явного флага считаются проверенными (исходные акты справочника).
        verified=n.get("verified", True),
        updated_by=n.get("updated_by"),
        updated_at=n.get("updated_at"),
    )


class NormPatch(BaseModel):
    status: Literal["active", "repealed", "partially"] | None = None
    since: str | None = None
    # Строка или список наименований, если акт заменён несколькими.
    replaced_by: str | list[str] | None = None
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
def update_norm(norm_id: str, body: NormPatch, admin: AdminUser, db: DB) -> NormOut:
    row = db.get(Norm, norm_id)
    if row is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Акт не найден в справочнике")
    data = dict(row.data)
    changes = body.model_dump(exclude_unset=True)
    titles = {n["title"] for n in load_norms()}
    if any(t not in titles for t in replacements(changes)):
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
    # Отметка правки: seed() не перезапишет такую запись из norms.json.
    data["updated_by"] = admin.username
    data["updated_at"] = datetime.now(UTC).isoformat(timespec="seconds")
    db.add(NormChange(norm_id=norm_id, changed_by=admin.username, before=row.data, after=data))
    row.data = data
    db.commit()
    norms_store.refresh()
    return _to_out(data)


@router.get("/export")
def export_norms(_: AdminUser) -> JSONResponse:
    """Текущий справочник в формате norms.json: для git, сверки скриптами и резервной копии."""
    return JSONResponse(
        {"norms": norms_store.export()},
        headers={"Content-Disposition": 'attachment; filename="norms.json"'},
    )


@router.get("/{norm_id}/history", response_model=list[NormChangeOut])
def norm_history(norm_id: str, _: AdminUser, db: DB) -> list[NormChange]:
    return list(
        db.scalars(
            select(NormChange)
            .where(NormChange.norm_id == norm_id)
            .order_by(NormChange.changed_at.desc(), NormChange.id.desc())
        )
    )


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
