import asyncio
import json

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
    found = normcheck.find_references("согласно 152-ФЗ и 124-ФЗ, ГОСТ Р 7.0.97-2016")
    assert [(f["norm_id"], f["status"]) for f in found] == [
        ("fz-152", "active"),
        (None, "unknown"),
        (None, "unknown"),
    ]


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
