"""Справочник актов: источник для проверки, с правкой из админки.

База — backend/app/data/norms.json (снимок в git). При старте записи переносятся в таблицу
`norms`, если она пуста; правки администратора идут в БД. Проверка читает объединённый список
из БД, с откатом на файл для скриптов и тестов, запускаемых без поднятого приложения.

ponytail: кэш в памяти процесса; при нескольких воркерах правку увидят после refresh/рестарта.
"""

import json
import logging
import re
from functools import lru_cache
from pathlib import Path

from sqlalchemy import select
from sqlalchemy.exc import SQLAlchemyError

from app.db import Norm, SessionLocal

DATA = Path(__file__).resolve().parent.parent / "data"
logger = logging.getLogger(__name__)
# Выставляет seed() при старте приложения: дальше справочник берётся только из БД, иначе
# сбой БД незаметно подменил бы правки юриста снимком из образа.
_db_required = False


def _compile(norms: list[dict]) -> list[dict]:
    for n in norms:
        n["_re"] = re.compile("|".join(f"(?:{p})" for p in n["patterns"]), re.IGNORECASE)
    return norms


def _from_file() -> list[dict]:
    return json.loads((DATA / "norms.json").read_text(encoding="utf-8"))["norms"]


@lru_cache
def load_norms() -> list[dict]:
    """Актуальный справочник со скомпилированными регулярками.

    Из БД; скрипты и бенч, запущенные без приложения, читают файл-снимок.
    """
    try:
        with SessionLocal() as s:
            rows = list(s.scalars(select(Norm).order_by(Norm.norm_id)))
    except SQLAlchemyError:
        if _db_required:
            # Исключение не кэшируется: следующий запрос снова пойдёт в БД.
            logger.exception("Справочник актов недоступен в БД")
            raise
        logger.warning("БД недоступна — справочник читается из norms.json")
        rows = []
    if rows:
        return _compile([dict(r.data) for r in rows])
    return _compile([dict(n) for n in _from_file()])


def refresh() -> None:
    load_norms.cache_clear()


def seed() -> None:
    """Перенести снимок из norms.json в БД, если таблица пуста."""
    global _db_required
    _db_required = True
    with SessionLocal() as s:
        if s.scalar(select(Norm).limit(1)) is not None:
            return
        for n in _from_file():
            s.add(Norm(norm_id=n["id"], data=n))
        s.commit()
    refresh()
