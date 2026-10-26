"""Отслеживание изменений актов из norms.json по порталу publication.pravo.gov.ru.

Запуск: uv run python scripts/pravo_sync.py [--since ГГГГ-ММ-ДД] [--blocks president government]
Токен не нужен: API официального опубликования открыт и работает только на чтение.

Фильтра по дате у API нет, поэтому документы листаются от новых к старым (PageSize=30) до даты
--since (по умолчанию — 30 дней). Название каждого документа сверяется с теми же шаблонами
`patterns`, что и в проверке документов (app.services.normcheck): если опубликован акт
«О внесении изменений в Федеральный закон … № 59-ФЗ …» — отслеживаемый 59-ФЗ помечается на
проверку. Справочник скрипт не меняет; решение принимает юрист (docs/NORMS.md).
Код выхода 1, если есть что проверить, — удобно для запуска по расписанию.
"""

import argparse
import re
import sys
from datetime import date, datetime, timedelta
from pathlib import Path

import httpx

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.services import normcheck  # noqa: E402

# Блоки публикации, где выходят изменения федеральных актов (…/api/PublicBlocks — весь список).
DEFAULT_BLOCKS = ("president", "government", "federal_authorities")
PAGE_SIZE = 30  # единственное допустимое значение PageSize у API
MAX_PAGES = 400  # страховка от бесконечного листания (12 000 документов на блок)
# Документ интересен, только если это изменение/отмена, а не первичная публикация акта.
CHANGE_RE = re.compile(r"внесени|утратив|призна|отмен|прекращен", re.IGNORECASE)


def fetch_changes(
    client: httpx.Client, blocks: tuple[str, ...], since: date, norms: list[dict]
) -> list[dict]:
    """Опубликованные с даты since документы, чьи названия ссылаются на отслеживаемые акты."""
    hits: list[dict] = []
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
                name = doc["complexName"].replace("\n", " ")
                if not CHANGE_RE.search(name):
                    continue
                for n in norms:
                    key = (n["id"], doc["eoNumber"])
                    if n["_re"].search(name) and key not in seen:
                        seen.add(key)
                        hits.append(
                            {
                                "norm_id": n["id"],
                                "date": published.isoformat(),
                                "name": name.strip(),
                                "url": f"http://publication.pravo.gov.ru/document/{doc['eoNumber']}",
                            }
                        )
            if stop:
                break
    hits.sort(key=lambda h: (h["norm_id"], h["date"]))
    return hits


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--since", type=date.fromisoformat, default=date.today() - timedelta(30))
    parser.add_argument("--blocks", nargs="+", default=list(DEFAULT_BLOCKS))
    parser.add_argument("--base-url", default="http://publication.pravo.gov.ru")
    args = parser.parse_args()
    norms = normcheck.load_norms()
    try:
        with httpx.Client(base_url=args.base_url, timeout=30) as client:
            hits = fetch_changes(client, tuple(args.blocks), args.since, norms)
    except httpx.HTTPError as e:
        print(f"Портал недоступен: {e}", file=sys.stderr)
        return 2
    print(
        f"Отслеживается актов: {len(norms)}; публикаций с {args.since.isoformat()} "
        f"по отслеживаемым актам: {len(hits)}"
    )
    for h in hits:
        print(f"- {h['norm_id']}: {h['date']} — {h['name']}\n  {h['url']}")
    if hits:
        print("\nСверьте статус и facts этих актов (docs/NORMS.md).")
    return 1 if hits else 0


if __name__ == "__main__":
    sys.exit(main())
