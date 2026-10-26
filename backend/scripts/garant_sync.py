"""Сверка справочника norms.json с системой ГАРАНТ через API «Гарант Коннект».

Запуск: GARANT_TOKEN=... uv run python scripts/garant_sync.py [--since ГГГГ-ММ-ДД]
Справочник скрипт не меняет: печатает расхождения статусов и изменения актов с даты --since
(по умолчанию — за 30 дней), решение о правке принимает юрист (docs/NORMS.md).
Код выхода 1, если есть что проверить, — удобно для запуска по расписанию.

Документ в ГАРАНТ ищется по наименованию акта; если найден не тот, номер документа
указывается вручную полем garant_topic в записи norms.json.
Лимиты Интернет-версии: «Информация о документе» — 300 вызовов в месяц (один на акт за запуск),
«Документы на контроле» — до 1000 в месяц вместе с простановкой ссылок.
"""

import argparse
import json
import os
import re
import sys
from datetime import date, timedelta
from pathlib import Path

import httpx

NORMS = Path(__file__).resolve().parent.parent / "app" / "data" / "norms.json"

# Поле status в «Информации о документе».
STATUSES = {
    "Действующие": "active",
    "Утратившие силу": "repealed",
    "Не вступившие в силу": "pending",
}
EVENTS = {
    1: "утратил силу",
    2: "изменён",
    3: "вступил в силу",
    4: "опубликована будущая редакция",
    5: "вступила в силу новая редакция",
}
HTTP_ERRORS = {
    401: "неверный или просроченный токен",
    403: "у токена нет прав на этот метод",
    423: "исчерпан месячный лимит вызовов",
    429: "больше 20 запросов в секунду",
}


def make_client(base_url: str, token: str, **kwargs) -> httpx.Client:
    return httpx.Client(
        base_url=base_url,
        headers={
            "Accept": "application/json",
            "Content-type": "application/json",
            "Authorization": f"Bearer {token}",
        },
        timeout=30,
        **kwargs,
    )


def _number(title: str) -> str | None:
    """Номер акта из наименования: «59-ФЗ», «6.30-2003», «2.2.2/2.4.1340-03»."""
    m = re.search(r"№\s*([\w.\-/]+)", title) or re.search(
        r"(?:ГОСТ\s*Р|СанПиН|СП)\s*([\d./-]*\d)", title
    )
    return m.group(1) if m else None


def find_topic(api: httpx.Client, norm: dict) -> tuple[int | None, str]:
    """Внутренний номер документа в ГАРАНТ и его наименование там."""
    if norm.get("garant_topic"):
        return norm["garant_topic"], ""
    r = api.post(
        "/v2/search",
        json={"text": norm["title"], "env": "internet", "sort": 0, "sortOrder": 0, "page": 1},
    )
    r.raise_for_status()
    number = _number(norm["title"])
    for doc in r.json().get("documents", [])[:10]:
        # Номер должен стоять отдельно: «59-ФЗ» не совпадает с «159-ФЗ».
        if number is None or re.search(rf"(?<![\d.]){re.escape(number)}(?!\d)", doc["name"]):
            return doc["topic"], doc["name"]
    return None, ""


def check(api: httpx.Client, norms: list[dict], since: date) -> list[str]:
    """Замечания по справочнику: не найден, статус расходится, акт менялся с даты since."""
    issues: list[str] = []
    by_topic: dict[int, dict] = {}
    for n in norms:
        topic, found_as = find_topic(api, n)
        if topic is None:
            issues.append(f"{n['id']}: не найден в ГАРАНТ — укажите garant_topic вручную")
            continue
        by_topic[topic] = n
        r = api.get(f"/v2/topic/{topic}")
        r.raise_for_status()
        info = r.json()
        theirs = STATUSES.get(info.get("status", ""), info.get("status"))
        # Акт, применяемый ограниченно, формально действует.
        ours = "active" if n["status"] == "partially" else n["status"]
        if theirs != ours:
            issues.append(
                f"{n['id']}: в справочнике «{n['status']}», в ГАРАНТ «{info.get('status')}»"
                f" — {info.get('name') or found_as} (topic {topic}"
                + (f", период действия {info['active']})" if info.get("active") else ")")
            )
    topics = list(by_topic)
    for i in range(0, len(topics), 100):  # не больше 100 документов за вызов
        r = api.post(
            "/v2/find-modified",
            json={"topics": topics[i : i + 100], "modDate": since.isoformat(), "needEvents": True},
        )
        r.raise_for_status()
        for t in r.json().get("topics", []):
            if t.get("modStatus") != 1:
                continue
            events = [
                f"{e['date']} {EVENTS.get(e['type'], e['type'])}"
                for e in t.get("events", [])
                if e["date"] >= since.isoformat()
            ]
            issues.append(
                f"{by_topic[t['topic']]['id']}: изменения с {since.isoformat()}"
                + (f" — {', '.join(events)}" if events else "")
                + "; сверьте facts и статус"
            )
    return issues


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--since", type=date.fromisoformat, default=date.today() - timedelta(30))
    args = parser.parse_args()
    token = os.environ.get("GARANT_TOKEN")
    if not token:
        print("Нужен GARANT_TOKEN — его выдаёт обслуживающая организация ГАРАНТ.", file=sys.stderr)
        return 2
    norms = json.loads(NORMS.read_text(encoding="utf-8"))["norms"]
    base_url = os.environ.get("GARANT_API_URL", "https://api.garant.ru")
    try:
        with make_client(base_url, token) as api:
            issues = check(api, norms, args.since)
    except httpx.HTTPStatusError as e:
        code = e.response.status_code
        print(
            f"ГАРАНТ ответил {code}: {HTTP_ERRORS.get(code, e.response.text[:200])}",
            file=sys.stderr,
        )
        return 2
    print(f"Сверено актов: {len(norms)}, замечаний: {len(issues)}")
    for line in issues:
        print(f"- {line}")
    return 1 if issues else 0


if __name__ == "__main__":
    sys.exit(main())
