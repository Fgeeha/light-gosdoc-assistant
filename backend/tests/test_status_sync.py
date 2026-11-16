import re
from urllib.parse import parse_qs

import httpx

from app.services import norms_store, status_sync


def ips_list(rows: list[tuple[str, str, str]]) -> str:
    """Выдача ИПС: [(nd, статус, «вид от дата № номер»)] — разметка как на сайте."""
    return "".join(
        f'<table class="list_elem even"><tr><td class="l_top"><span class="tiny_italic_bold">\n'
        f"\t\t{status}\n\t\t</span></td></tr><tr><td>"
        f'<a id="link_{i}" href="?docbody=&link_id={i}&nd={nd}&firstDoc=1" target="contents" '
        f'class="bold">\n\t{title}\n\t</a></td></tr></table>'
        for i, (nd, status, title) in enumerate(rows)
    )


def ips_card(*editions: str) -> str:
    opts = "".join(f"<option value='{i},1'>{e}</option>" for i, e in enumerate(editions))
    return f'<select name="doc_editions" onchange="x">{opts}</select>'


def gost_list(rows: list[tuple[str, str, str]]) -> str:
    return "".join(
        f'<div class="resultsInd"><h2 class="H2SearchResultsTitle">'
        f'<a href="/catalog/Details/?id={gid}"><span class="textBlue">{d}</span></a></h2>'
        f'<span class="text12Grey"><a href="/catalog/DetailsPG/?id={gid}" id="aDocIntro{gid}">'
        f"Термины и определения</a></span><strong>Статус: </strong> "
        f'<strong class="textBrightBlue">{status}</strong></div>'
        for gid, d, status in rows
    )


IPS = {  # (дата, номер) → выдача
    ("20.02.1995", "24-ФЗ"): ips_list(
        [("11", "Утратил силу", "Федеральный закон от 20.02.1995 № 24-ФЗ")]
    ),
    ("02.05.2006", "59-ФЗ"): ips_list(
        [("22", "Действует c изменениями", "Федеральный закон от 02.05.2006 № 59-ФЗ")]
    ),
    ("21.07.1993", "5485-I"): ips_list(
        [("33", "Действует c изменениями", "Закон РФ от 21.07.1993 № 5485-I")]
    ),
}
CARDS = {
    "11": ips_card("Исходная редакция", "2 - от 27.07.2006 № 149-ФЗ (не действ.)"),
    "22": ips_card("Исходная редакция", "10 - от 28.12.2024 № 547-ФЗ (изм.)"),
    "33": ips_card("Исходная редакция"),
}
GOST = {
    "7.0.8-2013": [("a1", "ГОСТ Р 7.0.8-2013", "Заменен")],
    "7.0.8": [
        ("a1", "ГОСТ Р 7.0.8-2013", "Заменен"),
        ("a2", "ГОСТ Р 7.0.8-2025", "Действует"),
        ("a3", "ГОСТ Р 7.0.80-2023", "Действует"),  # другой номер, не преемник
    ],
    "51141-98": [("b1", "ГОСТ Р 51141-98", "Заменен")],
}
EXPIRY = {"a1": "01.03.2025", "b1": "01.03.2014"}


def handler(request: httpx.Request) -> httpx.Response:
    if request.url.host == "pravo.gov.ru":
        q = parse_qs(request.url.query.decode(), keep_blank_values=True, encoding="cp1251")
        page = (
            IPS.get((q["a7date"][0], q["a8"][0]), "") if "list_itself" in q else CARDS[q["nd"][0]]
        )
        return httpx.Response(200, content=page.encode("cp1251"))
    if request.url.path == "/catalog/gostlist/":
        return httpx.Response(200, text=gost_list(GOST.get(request.url.params["searchString"], [])))
    gid = request.url.params["id"]
    return httpx.Response(200, text=f"<td>Дата огр. срока действия</td><td>{EXPIRY[gid]}</td>")


def run(norms):
    with httpx.Client(transport=httpx.MockTransport(handler)) as client:
        return status_sync.sync(norms, client)


NORMS = [
    {
        "id": "fz-24-1995",
        "title": "Федеральный закон от 20.02.1995 № 24-ФЗ «Об информации»",
        "status": "active",
    },
    {
        "id": "fz-59",
        "title": "Федеральный закон от 02.05.2006 № 59-ФЗ «Об обращениях»",
        "status": "active",
        "ips_revision": "9 - от 04.08.2023 № 480-ФЗ (изм.)",
        "_re": object(),  # скомпилированная регулярка не должна попасть в запись
    },
    {
        "id": "zakon-5485-1",
        "title": "Закон Российской Федерации от 21.07.1993 № 5485-1 «О тайне»",
        "status": "active",
    },
    {"id": "gost-7.0.8-2013", "title": "ГОСТ Р 7.0.8-2013 «Термины»", "status": "active"},
    {
        "id": "gost-51141-98",
        "title": "ГОСТ Р 51141-98 «Термины»",
        "status": "repealed",
        "since": "01.01.2014",
        "replaced_by": "ГОСТ Р 7.0.8-2013 «Термины»",
    },
    {
        "id": "koap-rsfsr",
        "title": "Кодекс РСФСР об административных правонарушениях",
        "status": "repealed",
    },
]


def test_sync_applies_changes_and_marks_records_unverified():
    records, report = run(NORMS)
    by_id = {n["id"]: n for n in records}
    changed = {r["norm_id"] for r in report["changed"]}

    # ИПС: акт утратил силу — статус и дата из редакции «(не действ.)»
    fz24 = by_id["fz-24-1995"]
    assert (fz24["status"], fz24["since"], fz24["verified"]) == ("repealed", "27.07.2006", False)
    assert "утратил силу" in fz24["sync_note"]
    # ИПС: статус совпал, но вышла новая редакция — запись на проверку, статус прежний
    fz59 = by_id["fz-59"]
    assert fz59["status"] == "active" and "новая редакция" in fz59["sync_note"]
    assert fz59["ips_revision"].startswith("10 ") and "_re" not in fz59
    # Закон РФ с номером «-1» найден как «-I»; совпадение — без пометки
    zakon = by_id["zakon-5485-1"]
    assert zakon["ips_nd"] == "33" and "sync_note" not in zakon and "zakon-5485-1" not in changed

    # ГОСТ: заменён — статус, точная дата и действующий стандарт с тем же номером
    g708 = by_id["gost-7.0.8-2013"]
    assert (g708["status"], g708["since"]) == ("repealed", "01.03.2025")
    assert g708["replaced_by"] == "ГОСТ Р 7.0.8-2025 «Термины и определения»"
    # ГОСТ уже отменён: дата поправлена, устаревшая замена заменена действующей
    g511 = by_id["gost-51141-98"]
    assert g511["since"] == "01.03.2014" and g511["replaced_by"].startswith("ГОСТ Р 7.0.8-2025")

    # действующей замены не было в справочнике — сверка добавила о ней запись
    new = by_id["gost-7.0.8-2025"]
    assert new["title"] == g708["replaced_by"] and new["status"] == "active"
    assert new["verified"] is False and "facts" not in new
    assert (
        re.search(new["patterns"][0], "по ГОСТ Р 7.0.8-2025 и ГОСТ Р 7.0.80-2023").group(0)
        == "ГОСТ Р 7.0.8-2025"
    )
    assert report["unchecked"] == ["koap-rsfsr"]  # нет номера и даты — не сверяется
    assert report["checked"] == len(NORMS) - 1
    assert len(records) == len(NORMS) + 1
    assert NORMS[0]["status"] == "active"  # исходный список не изменён


def test_follow_replacements_moves_link_past_repealed_replacement():
    norms = [
        {"id": "a", "title": "A", "status": "repealed", "replaced_by": "B"},
        {"id": "b", "title": "B", "status": "repealed", "replaced_by": "C"},
        {"id": "c", "title": "C", "status": "active"},
    ]
    results = status_sync._follow_replacements(norms, {})
    assert [r["norm_id"] for r in results] == ["a"]
    assert norms[0]["replaced_by"] == "C" and norms[0]["verified"] is False


def test_sync_endpoint_saves_and_patch_confirms(admin, monkeypatch):
    original = next(n for n in norms_store.load_norms() if n["id"] == "fz-98-2004")
    original = {k: v for k, v in original.items() if not k.startswith("_")}

    def fake_run(norms):
        records = [{k: v for k, v in n.items() if not k.startswith("_")} for n in norms]
        target = next(n for n in records if n["id"] == "fz-98-2004")
        target |= {"verified": False, "sync_note": "проверка"}
        result = {"norm_id": "fz-98-2004", "changes": {}, "note": "проверка"}
        return records, {"checked": len(records), "changed": [result], "unchecked": []}

    monkeypatch.setattr(status_sync, "run", fake_run)
    try:
        r = admin.post("/api/admin/norms/sync")
        assert r.status_code == 200, r.text
        assert r.json()["changed"][0]["norm_id"] == "fz-98-2004"
        row = next(n for n in admin.get("/api/admin/norms").json() if n["id"] == "fz-98-2004")
        assert (row["verified"], row["sync_note"]) == (False, "проверка")
        # Подтверждение в админке снимает пояснение автосверки
        r = admin.patch("/api/admin/norms/fz-98-2004", json={"status": "active"})
        assert (r.json()["verified"], r.json()["sync_note"]) == (True, None)
    finally:
        current = next(n for n in norms_store.load_norms() if n["id"] == "fz-98-2004")
        norms_store.save([original], [{k: v for k, v in current.items() if not k.startswith("_")}])


def test_save_keeps_edit_made_during_sync(admin):
    """Правка юриста во время сверки не откатывается: пишутся только поля, изменённые сверкой."""
    before = [
        {k: v for k, v in n.items() if not k.startswith("_")} for n in norms_store.load_norms()
    ]
    original = next(n for n in before if n["id"] == "fz-7-1996")
    # пока шла сверка, юрист подтвердил запись с другим статусом
    admin.patch("/api/admin/norms/fz-7-1996", json={"status": "partially"})
    synced = [dict(n) for n in before]
    next(n for n in synced if n["id"] == "fz-7-1996")["ips_nd"] = "777"
    try:
        norms_store.save(synced, before)
        row = next(n for n in norms_store.load_norms() if n["id"] == "fz-7-1996")
        assert (row["status"], row["ips_nd"]) == ("partially", "777")
    finally:
        norms_store.save([original], [{"id": "fz-7-1996"}])


def test_ips_find_rejects_conflicting_duplicates_and_accepts_identical():
    same = ips_list([("1", "Действует c изменениями", "ФЗ от 29.12.2012 № 273-ФЗ")] * 2)
    mixed = ips_list(
        [
            ("1", "Действует c изменениями", "ФЗ от 29.12.2012 № 273-ФЗ"),
            ("2", "Утратил силу", "ФЗ от 29.12.2012 № 273-ФЗ"),
        ]
    )
    for page, expected in ((same, "1"), (mixed, None)):
        transport = httpx.MockTransport(
            lambda r, p=page: httpx.Response(200, content=p.encode("cp1251"))
        )
        with httpx.Client(transport=transport) as client:
            found = status_sync.ips_find(client, "29.12.2012", "273-ФЗ")
        assert (found or {}).get("nd") == expected


def test_reference_and_year_parsing():
    assert status_sync._REF_RE.search("Закон от 02.05.2006 № 59-ФЗ, ред.").groups() == (
        "02.05.2006",
        "59-ФЗ",
    )
    assert status_sync._REF_RE.search("Указ от 01.02.2003 N 12)").group(2) == "12"
    # год сравнивается числом: стандарт 2025 года новее стандарта 1999 года
    assert status_sync._year("ГОСТ Р 1.0-2025") > status_sync._year("ГОСТ Р 1.0-99")


def test_synced_fields_survive_seed(admin):
    """Изменение автосверки не откатывается слиянием с norms.json при старте."""
    before = [
        {k: v for k, v in n.items() if not k.startswith("_")} for n in norms_store.load_norms()
    ]
    original = next(n for n in before if n["id"] == "fz-38-2006")
    synced = [dict(n) for n in before]
    target = next(n for n in synced if n["id"] == "fz-38-2006")
    target |= {"status": "repealed", "verified": False, "sync_note": "проверка"}
    try:
        norms_store.save(synced, before)
        norms_store.seed()
        row = next(n for n in norms_store.load_norms() if n["id"] == "fz-38-2006")
        assert (row["status"], row["sync_note"]) == ("repealed", "проверка")
        assert "sync_note" not in next(n for n in norms_store.export() if n["id"] == "fz-38-2006")
    finally:
        from app.db import Norm, SessionLocal

        with SessionLocal() as s:
            s.get(Norm, "fz-38-2006").data = original
            s.commit()
        norms_store.refresh()
