import sys
from datetime import date
from pathlib import Path

import httpx

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts"))

from app.services import sources  # noqa: E402


def _page(items):
    return {"items": items, "itemsTotalCount": len(items), "pagesTotalCount": 1}


def doc(eo, d, name):
    return {"eoNumber": eo, "publishDateShort": f"{d}T00:00:00", "complexName": name}


def make_handler(pages: dict[str, list[list[dict]]]):
    def handler(request: httpx.Request) -> httpx.Response:
        block = request.url.params["block"]
        index = int(request.url.params["Index"])
        assert int(request.url.params["PageSize"]) == 30
        block_pages = pages.get(block, [])
        items = block_pages[index - 1] if index <= len(block_pages) else []
        return httpx.Response(200, json=_page(items))

    return handler


def test_fetch_changes_matches_tracked_acts_and_stops_by_date():
    pages = {
        "president": [
            [
                doc(
                    "1",
                    "2026-10-20",
                    'Федеральный закон № 300-ФЗ "О внесении изменений в Федеральный закон '
                    '"О порядке рассмотрения обращений граждан Российской Федерации"',
                ),
                doc("2", "2026-10-18", 'Указ № 700 "Об утверждении состава комиссии"'),  # нет акта
                doc(
                    "3",
                    "2026-10-15",
                    'Федеральный закон № 290-ФЗ "О внесении изменений в Федеральный закон '
                    '"О персональных данных" (152-ФЗ)"',
                ),
            ],
            [doc("4", "2026-09-01", "О внесении изменений в 59-ФЗ")],  # старее since
        ],
    }
    api = httpx.Client(base_url="http://x", transport=httpx.MockTransport(make_handler(pages)))
    hits = sources.fetch_pravo_changes(api, date(2026, 10, 1), ("president",))
    ids = [(h["norm_id"], h["date"]) for h in hits]
    assert ("fz-59", "2026-10-20") in ids
    assert ("fz-152", "2026-10-15") in ids
    # документ за 01.09 не считан — листание остановилось по дате
    assert all(h["date"] >= "2026-10-01" for h in hits)
    fz59 = next(h for h in hits if h["norm_id"] == "fz-59")
    assert fz59["url"] == "http://publication.pravo.gov.ru/document/1"


def test_change_keyword_filter_skips_primary_publications():
    pages = {
        "president": [
            [
                doc(
                    "9",
                    "2026-10-10",
                    'Федеральный закон № 59-ФЗ "О порядке рассмотрения обращений граждан '
                    'Российской Федерации"',
                )
            ]
        ]
    }
    api = httpx.Client(base_url="http://x", transport=httpx.MockTransport(make_handler(pages)))
    hits = sources.fetch_pravo_changes(api, date(2026, 10, 1), ("president",))
    assert hits == []  # первичная публикация без слов об изменении не попадает в отчёт
