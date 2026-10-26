"""Справочник нормативных актов для администратора: просмотр и проверка обновлений.

Правка справочника пока только в norms.json (см. docs/NORMS.md): файл запекается в образ,
а изменение статуса должно проходить через юриста. Здесь — чтение справочника и поиск
опубликованных изменений по pravo.gov.ru, чтобы было видно, что пора проверить.
"""

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


class NormOut(BaseModel):
    id: str
    title: str
    status: str
    since: str | None = None
    replaced_by: str | None = None
    facts_count: int
    verified: bool


class NormPatch(BaseModel):
    status: Literal["active", "repealed", "partially"] | None = None
    since: str | None = None
    replaced_by: str | None = None
    facts: list[str] | None = None
    # Отметка «проверено юристом»: правка из UI подтверждает запись.
    verified: bool | None = None


class Update(BaseModel):
    norm_id: str
    date: str
    name: str
    url: str


@router.get("", response_model=list[NormOut])
def list_norms(_: AdminUser) -> list[NormOut]:
    return [
        NormOut(
            id=n["id"],
            title=n["title"],
            status=n["status"],
            since=n.get("since"),
            replaced_by=n.get("replaced_by"),
            facts_count=len(n.get("facts") or []),
            # Записи без явного флага считаются проверенными (исходные 19 актов).
            verified=n.get("verified", True),
        )
        for n in load_norms()
    ]


@router.patch("/{norm_id}", response_model=NormOut)
def update_norm(norm_id: str, body: NormPatch, _: AdminUser, db: DB) -> NormOut:
    row = db.get(Norm, norm_id)
    if row is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Акт не найден в справочнике")
    data = dict(row.data)
    changes = body.model_dump(exclude_unset=True)
    for key in ("status", "since", "replaced_by", "facts"):
        if key in changes:
            if changes[key] in (None, [], ""):
                data.pop(key, None)
            else:
                data[key] = changes[key]
    # Любая правка из UI — это подтверждение записи, если прямо не сказано иное.
    data["verified"] = changes.get("verified", True)
    row.data = data
    db.commit()
    norms_store.refresh()
    return NormOut(
        id=data["id"],
        title=data["title"],
        status=data["status"],
        since=data.get("since"),
        replaced_by=data.get("replaced_by"),
        facts_count=len(data.get("facts") or []),
        verified=data["verified"],
    )


@router.get("/updates", response_model=list[Update])
def check_updates(_: AdminUser, days: int = Query(30, ge=1, le=365)) -> list[sources.Change]:
    """Публикации за последние `days` дней, затрагивающие отслеживаемые акты (pravo.gov.ru)."""
    since = date.today() - timedelta(days)
    # Законы и постановления; агентские акты (federal_authorities) слишком объёмны для онлайн-кнопки.
    blocks = ("president", "government")
    try:
        with httpx.Client(base_url=sources.PRAVO_BASE_URL, timeout=30) as client:
            return sources.fetch_pravo_changes(client, since, blocks)
    except httpx.HTTPError as e:
        raise HTTPException(
            status.HTTP_502_BAD_GATEWAY, f"Портал опубликования недоступен: {e}"
        ) from e
