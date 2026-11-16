"""Справочник актов: источник для проверки, с правкой из админки.

База — backend/app/data/norms.json (снимок в git), рабочая копия — таблица `norms`.
При старте файл сливается с БД (seed): новые записи добавляются, записи без правок из админки
обновляются, правки юриста (поле updated_by) не трогаются. Проверка читает БД; скрипты и бенч,
запущенные без приложения, — файл.

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
    """Слить снимок norms.json с БД, не затирая правки из админки."""
    global _db_required
    _db_required = True
    added = updated = 0
    with SessionLocal() as s:
        rows = {r.norm_id: r for r in s.scalars(select(Norm))}
        for n in _from_file():
            row = rows.get(n["id"])
            if row is None:
                s.add(Norm(norm_id=n["id"], data=n))
                added += 1
            elif "updated_by" not in row.data and row.data != n:
                row.data = n  # запись не правили в админке — источник истины файл
                updated += 1
        s.commit()
    if added or updated:
        logger.info("Справочник из norms.json: добавлено %d, обновлено %d", added, updated)
    refresh()


def export() -> list[dict]:
    """Справочник в формате norms.json — без служебных полей вроде скомпилированных регулярок."""
    return [{k: v for k, v in n.items() if not k.startswith("_")} for n in load_norms()]
