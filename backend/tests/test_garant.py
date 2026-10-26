import json
import sys
from datetime import date
from pathlib import Path

import httpx

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts"))
import garant_sync  # noqa: E402

NORMS = [
    {"id": "fz-59", "status": "active", "title": "Федеральный закон от 02.05.2006 № 59-ФЗ «О…»"},
    {"id": "gost-6.30-2003", "status": "repealed", "title": "ГОСТ Р 6.30-2003 «Унифицированная…»"},
    {
        "id": "fz-294",
        "status": "partially",
        "title": "Федеральный закон № 294-ФЗ",
        "garant_topic": 3,
    },
    {"id": "fz-999", "status": "active", "title": "Федеральный закон № 999-ФЗ"},
]
SEARCH = {
    "Федеральный закон от 02.05.2006 № 59-ФЗ «О…»": [
        {"name": 'Федеральный закон от 2 мая 2006 г. N 159-ФЗ "…"', "topic": 9},
        {"name": 'Федеральный закон от 2 мая 2006 г. N 59-ФЗ "…"', "topic": 1},
    ],
    "ГОСТ Р 6.30-2003 «Унифицированная…»": [{"name": "ГОСТ Р 6.30-2003 …", "topic": 2}],
}
INFO = {1: "Утратившие силу", 2: "Утратившие силу", 3: "Действующие"}


def handler(request: httpx.Request) -> httpx.Response:
    assert request.headers["Authorization"] == "Bearer t"
    path = request.url.path
    if path == "/v2/search":
        docs = SEARCH.get(json.loads(request.content)["text"], [])
        return httpx.Response(200, json={"documents": docs})
    if path.startswith("/v2/topic/"):
        topic = int(path.rsplit("/", 1)[1])
        return httpx.Response(200, json={"name": f"док {topic}", "status": INFO[topic]})
    if path == "/v2/find-modified":
        body = json.loads(request.content)
        assert body["modDate"] == "2026-10-01" and sorted(body["topics"]) == [1, 2, 3]
        events = [{"date": "2010-03-30", "type": 4}, {"date": "2026-10-10", "type": 2}]
        topics = [{"topic": 3, "modStatus": 1, "events": events}, {"topic": 2, "modStatus": 2}]
        return httpx.Response(200, json={"topics": topics})
    return httpx.Response(404)


def test_garant_check_reports_status_mismatch_and_changes():
    api = garant_sync.make_client("https://api.test", "t", transport=httpx.MockTransport(handler))
    issues = garant_sync.check(api, NORMS, date(2026, 10, 1))
    assert issues == [
        "fz-59: в справочнике «active», в ГАРАНТ «Утратившие силу» — док 1 (topic 1)",
        "fz-999: не найден в ГАРАНТ — укажите garant_topic вручную",
        "fz-294: изменения с 2026-10-01 — 2026-10-10 изменён; сверьте facts и статус",
    ]


def test_garant_number_from_title():
    assert garant_sync._number("Федеральный закон от 02.05.2006 № 59-ФЗ «О…»") == "59-ФЗ"
    assert garant_sync._number("СанПиН 2.2.2/2.4.1340-03 «…»") == "2.2.2/2.4.1340-03"
    assert garant_sync._number("Кодекс РСФСР об административных правонарушениях") is None
