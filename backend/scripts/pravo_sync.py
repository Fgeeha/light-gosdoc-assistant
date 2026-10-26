"""Отслеживание изменений актов из norms.json по порталу publication.pravo.gov.ru.

Запуск: uv run python scripts/pravo_sync.py [--since ГГГГ-ММ-ДД] [--blocks president government]
Токен не нужен: API официального опубликования открыт и работает только на чтение.
Логика сверки — в app.services.sources (её же использует админка).
Код выхода 1, если есть что проверить, — удобно для запуска по расписанию.
"""

import argparse
import sys
from datetime import date, timedelta
from pathlib import Path

import httpx

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.services import sources  # noqa: E402


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--since", type=date.fromisoformat, default=date.today() - timedelta(30))
    parser.add_argument("--blocks", nargs="+", default=list(sources.DEFAULT_BLOCKS))
    parser.add_argument("--base-url", default=sources.PRAVO_BASE_URL)
    args = parser.parse_args()
    try:
        with httpx.Client(base_url=args.base_url, timeout=30) as client:
            hits = sources.fetch_pravo_changes(client, args.since, tuple(args.blocks))
    except httpx.HTTPError as e:
        print(f"Портал недоступен: {e}", file=sys.stderr)
        return 2
    print(f"Публикаций с {args.since.isoformat()} по отслеживаемым актам: {len(hits)}")
    for h in hits:
        print(f"- {h['norm_id']}: {h['date']} — {h['name']}\n  {h['url']}")
    if hits:
        print("\nСверьте статус и facts этих актов (docs/NORMS.md).")
    return 1 if hits else 0


if __name__ == "__main__":
    sys.exit(main())
