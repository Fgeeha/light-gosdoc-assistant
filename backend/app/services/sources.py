"""Сбор изменений нормативных актов из внешних систем.

Пока один источник — открытый API официального опубликования publication.pravo.gov.ru
(без токена, только чтение). Логика общая для CLI (scripts/pravo_sync.py) и админки.
"""

import re
from datetime import date, datetime
from typing import TypedDict

import httpx

from app.services.normcheck import load_norms

PRAVO_BASE_URL = "http://publication.pravo.gov.ru"
# Блоки публикации, где выходят изменения федеральных актов (…/api/PublicBlocks — весь список).
DEFAULT_BLOCKS = ("president", "government", "federal_authorities")
PAGE_SIZE = 30  # единственное допустимое значение PageSize у API
MAX_PAGES = 400  # страховка от бесконечного листания (12 000 документов на блок)
# Документ интересен, только если это изменение/отмена, а не первичная публикация акта.
_CHANGE_RE = re.compile(r"внесени|утратив|призна|отмен|прекращен", re.IGNORECASE)


class Change(TypedDict):
    norm_id: str
    date: str
    name: str
    url: str


def fetch_pravo_changes(
    client: httpx.Client,
    since: date,
    blocks: tuple[str, ...] = DEFAULT_BLOCKS,
    norms: list[dict] | None = None,
) -> list[Change]:
    """Опубликованные с даты since документы, чьи названия ссылаются на отслеживаемые акты.

    Фильтра по дате у API нет, поэтому документы листаются от новых к старым до даты since.
    """
    norms = norms if norms is not None else load_norms()
    hits: list[Change] = []
    seen: set[tuple[str, str]] = set()
    for block in blocks:
        for index in range(1, MAX_PAGES + 1):
            r = client.get(
                "/api/Documents",
                params={"block": block, "PageSize": PAGE_SIZE, "Index": index},
            )
            r.raise_for_status()
            items = r.json().get("items", [])
            if not items:
                break
            stop = False
            for doc in items:
                published = datetime.fromisoformat(doc["publishDateShort"]).date()
                if published < since:
                    stop = True
                    break
                name = doc["complexName"].replace("\n", " ").strip()
                if not _CHANGE_RE.search(name):
                    continue
                for n in norms:
                    key = (n["id"], doc["eoNumber"])
                    if n["_re"].search(name) and key not in seen:
                        seen.add(key)
                        hits.append(
                            Change(
                                norm_id=n["id"],
                                date=published.isoformat(),
                                name=name,
                                url=f"{PRAVO_BASE_URL}/document/{doc['eoNumber']}",
                            )
                        )
            if stop:
                break
    hits.sort(key=lambda h: (h["norm_id"], h["date"]))
    return hits
