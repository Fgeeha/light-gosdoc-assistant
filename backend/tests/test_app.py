import asyncio
import json

import httpx
import pytest
from fastapi.testclient import TestClient

from app import llm
from app.main import app
from app.services import files, normcheck, tables


def _events(body: str) -> list[tuple[str, object]]:
    out = []
    for block in body.strip().split("\n\n"):
        lines = dict(line.split(": ", 1) for line in block.splitlines())
        out.append((lines["event"], json.loads(lines["data"])))
    return out


def test_auth_required(client):
    client.cookies.clear()
    assert client.get("/api/auth/me").status_code == 401
    assert client.post("/api/tools/check", json={"text": "x"}).status_code == 401


def test_admin_creates_user_who_can_login_but_not_administer(admin):
    r = admin.post(
        "/api/admin/users",
        json={"username": "ivanov", "full_name": "Иванов И. И.", "password": "secret-pass"},
    )
    assert r.status_code == 201, r.text
    uid = r.json()["id"]
    assert (
        admin.post(
            "/api/admin/users", json={"username": "ivanov", "password": "secret-pass"}
        ).status_code
        == 409
    )

    with TestClient(app) as user:
        assert (
            user.post(
                "/api/auth/login", json={"username": "ivanov", "password": "wrong-pass"}
            ).status_code
            == 401
        )
        assert (
            user.post(
                "/api/auth/login", json={"username": "ivanov", "password": "secret-pass"}
            ).status_code
            == 200
        )
        assert user.get("/api/auth/me").json()["full_name"] == "Иванов И. И."
        assert user.get("/api/admin/users").status_code == 403

        admin.patch(f"/api/admin/users/{uid}", json={"is_active": False})
        assert user.get("/api/auth/me").status_code == 401

    assert admin.delete(f"/api/admin/users/{uid}").status_code == 204


def test_bootstrap_admin_rejects_bad_password(monkeypatch, caplog):
    from app import main
    from app.config import get_settings

    class EmptyDB:  # пользователей нет, создавать никого нельзя
        def __enter__(self):
            return self

        def __exit__(self, *_):
            return None

        def scalar(self, *_):
            return 0

        def add(self, _):
            raise AssertionError("администратор с плохим паролем не должен создаваться")

    monkeypatch.setattr(main, "SessionLocal", EmptyDB)
    for bad in ("short", "п" * 40):  # 5 символов; 80 байт
        monkeypatch.setattr(get_settings(), "admin_password", bad)
        main.bootstrap_admin()
        assert "ADMIN_PASSWORD" in caplog.text
        caplog.clear()


def test_api_docs_disabled_by_default(client):
    assert client.get("/api/openapi.json").status_code == 404
    assert client.get("/api/docs").status_code == 404


def test_admin_cannot_lock_self(admin):
    me = admin.get("/api/auth/me").json()
    assert admin.patch(f"/api/admin/users/{me['id']}", json={"is_admin": False}).status_code == 400
    assert admin.delete(f"/api/admin/users/{me['id']}").status_code == 400


def test_find_references_on_examples():
    raw = (normcheck.DATA / "examples" / "check.json").read_text(encoding="utf-8")
    examples = {e["id"]: e["text"] for e in json.loads(raw)}
    found = {
        f["norm_id"]: f["status"] for f in normcheck.find_references(examples["appeals-regulation"])
    }
    assert found == {
        "ukaz-1968-2534": "repealed",
        "fz-24-1995": "repealed",
        "gost-6.30-2003": "repealed",
        "koap-rsfsr": "repealed",
    }
    ukaz = normcheck.find_references(examples["appeals-regulation"])[0]
    assert any("30 дней" in fact for fact in ukaz["facts"])
    found = {f["norm_id"] for f in normcheck.find_references(examples["procurement-tz"])}
    assert {"fz-94", "fz-128", "fz-294", "gost-51141-98", "gost-6.30-2003"} <= found


def test_find_references_does_not_confuse_numbers():
    found = normcheck.find_references(
        "согласно 152-ФЗ и 124-ФЗ, ГОСТ Р 7.0.97-2016, ГОСТ Р 7.0.5-2008"
    )
    assert [(f["norm_id"], f["status"]) for f in found] == [
        ("fz-152", "active"),
        (None, "unknown"),
        ("gost-7.0.97-2016", "active"),
        (None, "unknown"),
    ]


def test_replaced_by_matches_a_title():
    # Иначе нормы нового акта молча не попадают в справку для модели.
    norms = normcheck.load_norms()
    titles = {n["title"] for n in norms}
    broken = [n["id"] for n in norms if any(t not in titles for t in normcheck.replacements(n))]
    assert broken == []


def test_replaced_by_list_joins_replacements_and_facts(admin):
    sp = "СП 2.2.3670-20 «Санитарно-эпидемиологические требования к условиям труда»"
    r = admin.patch("/api/admin/norms/sp-2.2.3670-20", json={"facts": ["Норма СП."]})
    assert r.status_code == 200
    f = next(f for f in normcheck.find_references("по СанПиН 2.2.2/2.4.1340-03") if f["norm_id"])
    assert f["norm_id"] == "sanpin-1340-03" and sp in f["replaced_by"] and "; " in f["replaced_by"]
    assert f["facts"] == ["Норма СП."]


def test_brief_without_since_and_replacement_has_no_none():
    f = normcheck.find_references("согласно 152-ФЗ")[0] | {
        "status": "repealed",
        "since": None,
        "replaced_by": None,
    }
    text = normcheck.build_messages("согласно 152-ФЗ", [f])[1]["content"]
    assert "None" not in text and "УТРАТИЛ СИЛУ. Замена в справочнике не указана." in text


def test_strip_think_across_chunk_boundaries():
    async def gen():
        for c in ["<thi", "nk>секрет</th", "ink>", "Ответ <", "b>", " готов"]:
            yield c

    async def collect():
        return "".join([c async for c in llm.strip_think(gen())])

    assert asyncio.run(collect()) == "Ответ <b> готов"

    async def orphan():
        for c in ["</th", "ink>\n\nТекст"]:
            yield c

    async def collect_orphan():
        return "".join([c async for c in llm.strip_think(orphan())])

    assert asyncio.run(collect_orphan()) == "\n\nТекст"


def test_strip_think_prefilled_template_hides_reasoning():
    async def gen():
        for c in ["Пользователь просит ", "проверить {draft}", "</think>", '{"rows": []}']:
            yield c

    async def collect():
        return "".join([c async for c in llm.strip_think(gen(), inside=True)])

    assert asyncio.run(collect()) == '{"rows": []}'


def test_long_cyrillic_password_is_rejected_not_500(admin):
    r = admin.post("/api/admin/users", json={"username": "longpass", "password": "ж" * 40})
    assert r.status_code == 422
    r = admin.post("/api/auth/login", json={"username": "admin", "password": "ж" * 100})
    assert r.status_code == 401


def test_logout_revokes_session(client):
    with TestClient(app) as c:
        c.post("/api/auth/login", json={"username": "admin", "password": "admin-password"})
        token = c.cookies.get("gosdoc_session")
        assert c.post("/api/auth/logout").status_code == 204
        c.cookies.set("gosdoc_session", token)
        assert c.get("/api/auth/me").status_code == 401


def test_parse_table_rejects_ambiguous_headers():
    with pytest.raises(ValueError):
        tables.parse_table("a;a\n1;2")
    with pytest.raises(ValueError):
        tables.parse_table("a;b\n1;2;3")


def test_parse_table_malformed_csv_is_value_error():
    # Незакрытая кавычка тянет поле за лимит csv: должна быть 422, а не 500.
    with pytest.raises(ValueError, match="Не удалось разобрать"):
        tables.parse_table('a;b\n"' + "x" * 200_000 + ";1")


def test_docx_strips_control_chars():
    assert files.to_docx("стр\x00ока\x07")


def test_parse_json_object_tolerates_trailing_text():
    assert llm.parse_json_object('```json\n{"rows": []}\n```\nПояснение: {x}') == {"rows": []}
    assert llm.parse_json_object('Ответ: [{"a": 1}]') == {"rows": [{"a": 1}]}


def test_parse_table_detects_delimiters():
    header, rows = tables.parse_table("a;b\n1;2\n\n3;4\n")
    assert header == ["a", "b"] and rows == [{"a": "1", "b": "2"}, {"a": "3", "b": "4"}]
    header, _ = tables.parse_table("x\ty\n1\t2")
    assert header == ["x", "y"]


def test_table_endpoint_streams_rows(admin, monkeypatch):
    async def fake(messages, model=None):
        n = len(json.loads(messages[1]["content"].split(":\n", 1)[1]))
        return {"rows": [{"A": i} for i in range(n)]}

    monkeypatch.setattr(llm, "complete_json", fake)
    source = "x\n" + "\n".join(str(i) for i in range(20))
    r = admin.post("/api/tools/table", json={"source": source, "columns": ["A", "B"]})
    events = _events(r.text)
    rows = [row for kind, data in events if kind == "rows" for row in data]
    assert len(rows) == 20 and rows[0] == {"A": "0", "B": ""}
    assert events[-1][0] == "done"


def test_llm_calls_are_limited(monkeypatch):
    from app.config import get_settings

    active = peak = 0

    async def server(request: httpx.Request) -> httpx.Response:
        nonlocal active, peak
        active += 1
        peak = max(peak, active)
        await asyncio.sleep(0.05)
        active -= 1
        body = 'data: {"choices": [{"delta": {"content": "ok"}}]}\n\ndata: [DONE]\n\n'
        return httpx.Response(200, text=body)

    real = httpx.AsyncClient
    monkeypatch.setattr(
        llm.httpx, "AsyncClient", lambda **kw: real(transport=httpx.MockTransport(server), **kw)
    )
    monkeypatch.setattr(llm, "_slots", None)
    monkeypatch.setattr(get_settings(), "llm_max_concurrency", 1)

    async def one():
        return "".join([c async for c in llm.stream_chat([{"role": "user", "content": "x"}])])

    async def three():
        return await asyncio.gather(one(), one(), one())

    assert asyncio.run(three()) == ["ok"] * 3
    assert peak == 1


def test_llm_errors_do_not_leak_details(admin, monkeypatch):
    def server(request: httpx.Request) -> httpx.Response:
        if request.url.path.endswith("/broken/v1/chat/completions"):
            return httpx.Response(200, text="data: {не json\n\n")
        return httpx.Response(401, json={"error": "org-secret-123: invalid key"})

    real = httpx.AsyncClient
    monkeypatch.setattr(
        llm.httpx, "AsyncClient", lambda **kw: real(transport=httpx.MockTransport(server), **kw)
    )
    from app.config import get_settings

    for base, expected in (
        ("http://m/v1", "ошибку 401"),
        ("http://m/broken/v1", "некорректный поток"),
    ):
        monkeypatch.setattr(get_settings(), "llm_base_url", base)
        r = admin.post("/api/tools/letter", json={"incoming": "Прошу ответить"})
        errors = [d["message"] for k, d in _events(r.text) if k == "error"]
        assert len(errors) == 1 and expected in errors[0] and "org-secret" not in errors[0]


def test_check_endpoint_streams_findings_and_rewrite(admin, monkeypatch):
    async def fake(messages, model=None):
        assert "149-ФЗ" in messages[1]["content"]
        yield "Исправлено"

    monkeypatch.setattr(llm, "stream_chat", fake)
    r = admin.post("/api/tools/check", json={"text": "Согласно 24-ФЗ от 20.02.1995"})
    kinds = [k for k, _ in _events(r.text)]
    assert kinds == ["findings", "delta", "done"]


def test_docx_roundtrip(admin):
    r = admin.post("/api/tools/docx", json={"text": "Строка 1\nСтрока 2", "filename": "ответ"})
    assert r.status_code == 200
    r = admin.post(
        "/api/tools/extract", files={"file": ("a.docx", r.content, "application/octet-stream")}
    )
    assert r.json()["text"] == "Строка 1\nСтрока 2"


def test_extract_limits(admin, monkeypatch):
    def upload(name: str, content: bytes):
        return admin.post(
            "/api/tools/extract", files={"file": (name, content, "application/octet-stream")}
        )

    monkeypatch.setattr(files, "MAX_UNPACKED_BYTES", 1000)  # обычный .docx распаковывается больше
    r = upload("a.docx", files.to_docx("текст"))
    assert r.status_code == 422 and "распаковки" in r.json()["detail"]

    monkeypatch.setattr(files, "MAX_TEXT_CHARS", 5)
    r = upload("a.txt", "длинный текст".encode())
    assert r.status_code == 422 and "символов" in r.json()["detail"]

    assert upload("a.docx", b"not a zip").status_code == 422


def test_examples_match_source_documents():
    import sys

    sys.path.insert(0, str(normcheck.DATA.parent.parent / "scripts"))
    import build_examples

    assert build_examples.build(write=False) == []


def test_example_docs_download(admin):
    r = admin.get("/api/tools/examples/docs/check/appeals-regulation.docx")
    assert r.status_code == 200 and r.content[:2] == b"PK"
    assert admin.get("/api/tools/examples/docs/check/..%2F..%2Fnorms.json").status_code == 404
    assert admin.get("/api/tools/examples/docs/../norms.json").status_code == 404


def test_admin_norms_list_and_updates(admin, monkeypatch):
    norms = admin.get("/api/admin/norms").json()
    assert len(norms) >= 19
    fz59 = next(n for n in norms if n["id"] == "fz-59")
    assert fz59["status"] == "active" and fz59["facts_count"] >= 1 and fz59["verified"] is True
    ukaz = next(n for n in norms if n["id"] == "ukaz-1968-2534")
    assert ukaz["status"] == "repealed" and ukaz["replaced_by"].startswith("Федеральный закон")

    from app.routers import norms as norms_router

    monkeypatch.setattr(
        norms_router.sources,
        "fetch_pravo_changes",
        lambda *a, **k: [
            {
                "norm_id": "fz-44",
                "date": "2026-08-04",
                "name": "ФЗ № 279-ФЗ о внесении изменений",
                "url": "http://x/1",
            }
        ],
    )
    updates = admin.get("/api/admin/norms/updates?days=90").json()
    assert updates == [
        {
            "norm_id": "fz-44",
            "date": "2026-08-04",
            "name": "ФЗ № 279-ФЗ о внесении изменений",
            "url": "http://x/1",
        }
    ]


def test_norms_do_not_fall_back_to_file_when_db_fails(admin, monkeypatch):
    from sqlalchemy.exc import OperationalError

    from app.services import norms_store

    def broken():
        raise OperationalError("select", {}, Exception("db down"))

    monkeypatch.setattr(norms_store, "SessionLocal", broken)
    norms_store.refresh()
    with pytest.raises(OperationalError):
        norms_store.load_norms()
    monkeypatch.undo()
    norms_store.refresh()
    assert norms_store.load_norms()  # после восстановления БД — снова из БД


def test_admin_norm_updates_portal_errors(admin, monkeypatch):
    from app.services import sources

    for exc, code in ((KeyError("eoNumber"), 502), (TimeoutError(), 504)):

        def fail(*_, exc=exc, **__):
            raise exc

        monkeypatch.setattr(sources, "fetch_pravo_changes", fail)
        assert admin.get("/api/admin/norms/updates").status_code == code


def test_admin_norm_edit_persists_and_affects_check(admin):
    from app.services import normcheck

    # 152-ФЗ действует — меняем статус из UI на «утратил силу».
    r = admin.patch("/api/admin/norms/fz-152", json={"status": "repealed", "since": "2030"})
    assert (
        r.status_code == 200 and r.json()["status"] == "repealed" and r.json()["verified"] is True
    )

    status = {f["norm_id"]: f["status"] for f in normcheck.find_references("согласно 152-ФЗ")}
    assert status["fz-152"] == "repealed"  # правка видна проверке

    # вернуть как было, чтобы не влиять на другие тесты
    admin.patch("/api/admin/norms/fz-152", json={"status": "active", "since": None})
    assert admin.patch("/api/admin/norms/fz-000", json={"status": "active"}).status_code == 404
    r = admin.patch("/api/admin/norms/fz-152", json={"replaced_by": "Закон о чём-то"})
    assert r.status_code == 422
    # verified: null раньше давало 500 при сборке ответа.
    assert admin.patch("/api/admin/norms/fz-152", json={"verified": None}).status_code == 422


def test_norm_edit_is_journaled_and_exported(admin):
    r = admin.patch("/api/admin/norms/fz-149", json={"facts": ["Тестовая норма."]})
    assert r.status_code == 200 and r.json()["updated_by"] == "admin"
    history = admin.get("/api/admin/norms/fz-149/history").json()
    assert history[0]["changed_by"] == "admin"
    assert "facts" not in history[0]["before"] or history[0]["before"]["facts"] != [
        "Тестовая норма."
    ]
    assert history[0]["after"]["facts"] == ["Тестовая норма."]

    exported = admin.get("/api/admin/norms/export")
    assert "attachment" in exported.headers["content-disposition"]
    norms = {n["id"]: n for n in exported.json()["norms"]}
    assert norms["fz-149"]["facts"] == ["Тестовая норма."]
    assert not any(k.startswith("_") for n in norms.values() for k in n)


def test_seed_merges_file_without_overwriting_admin_edits(admin):
    from app.db import Norm, SessionLocal
    from app.services import norms_store

    admin.patch("/api/admin/norms/fz-59", json={"comment": None, "verified": True})
    with SessionLocal() as s:
        s.delete(s.get(Norm, "fz-125"))  # как будто запись появилась в файле позже
        edited = s.get(Norm, "fz-59")
        edited.data = edited.data | {"status": "repealed"}  # правка юриста
        untouched = s.get(Norm, "fz-152")
        untouched.data = {k: v for k, v in untouched.data.items() if k != "updated_by"} | {
            "status": "repealed"
        }  # устаревшая копия без правок
        s.commit()
    norms_store.seed()
    by_id = {n["id"]: n for n in norms_store.load_norms()}
    assert "fz-125" in by_id
    assert by_id["fz-59"]["status"] == "repealed"
    assert by_id["fz-152"]["status"] == "active"
    admin.patch("/api/admin/norms/fz-59", json={"status": "active"})
