"""Автоматическая сверка статусов справочника с бесплатными официальными источниками.

- ИПС «Законодательство России» (pravo.gov.ru/proxy/ips): статус и последняя редакция актов,
  у которых в `title` (или в поле `ips_ref`) есть «от ДД.ММ.ГГГГ № N».
- Каталог стандартов ФГБУ «Институт стандартизации» (gostinfo.ru): статус ГОСТ, дата окончания
  действия и действующий стандарт с тем же номером.

API у обоих нет, разбирается HTML. Расхождения применяются к записи сразу, запись получает
`verified: false` и пояснение в `sync_note` — юрист подтверждает или исправляет её в админке.

CLI (работает и в контейнере, где нет scripts/): python -m app.services.status_sync [--apply]
Без --apply печатает отчёт, с --apply пишет в БД из DATABASE_URL. Код выхода 1 при изменениях.
--write-json сверяет файл-снимок norms.json и записывает результат в него (для git).
"""

import argparse
import html
import re
import sys
from concurrent.futures import ThreadPoolExecutor
from typing import TypedDict
from urllib.parse import quote

import httpx

from app.services.normcheck import replacements

IPS_URL = "http://pravo.gov.ru/proxy/ips/"
GOST_URL = "https://www.gostinfo.ru"
WORKERS = 4  # параллельных запросов: кнопка отвечает за ~минуту, сайты не нагружаются
REVISION_TIMEOUT = 20

_REF_RE = re.compile(r"от (\d{2}\.\d{2}\.\d{4}) (?:№|N) ?([^\s«»\",;:)]+)")
_GOST_RE = re.compile(r"ГОСТ(?: Р)?(?: ИСО)? ([\d.]+(?:-\d+)*-(\d{2,4}))(?!\d)")
_DATE_RE = re.compile(r"\d{2}\.\d{2}\.\d{4}")
_LABEL = {"active": "действует", "repealed": "утратил силу", "partially": "ограниченно"}


class Result(TypedDict):
    norm_id: str
    changes: dict
    note: str


class Report(TypedDict):
    checked: int
    changed: list[Result]
    unchecked: list[str]


def _text(fragment: str) -> str:
    return " ".join(html.unescape(re.sub(r"<[^>]+>", " ", fragment)).split())


# --- ИПС -------------------------------------------------------------------------------------


def _ips_get(client: httpx.Client, query: str, timeout: float | None = None) -> str:
    r = client.get(f"{IPS_URL}?{query}", timeout=timeout or client.timeout)
    r.raise_for_status()
    return r.content.decode("cp1251", "replace")


def ips_find(client: httpx.Client, day: str, number: str) -> dict | None:
    """Акт по дате и номеру: {nd, status}. None, если не найден или найденные расходятся в статусе."""
    page = _ips_get(
        client,
        f"list_itself=&bpas=cd00000&a8={quote(number.encode('cp1251'))}&a8type=1"
        f"&a7type=1&a7date={day}&sort=7&page=first&lstsize=20",
    )
    rows = []
    for block in page.split('class="list_elem')[1:]:
        status = re.search(r'tiny_italic_bold">([^<]+)<', block)
        link = re.search(r'<a id="link_\d+"[^>]*nd=(\d+)[^>]*>([\s\S]*?)</a>', block)
        # a8type=1 — «номер начинается с», поэтому номер сверяется точно.
        if status and link and _text(link.group(2)).endswith(f"№ {number}"):
            rows.append({"nd": link.group(1), "status": _text(status.group(1))})
    # В ИПС встречаются дубли одного акта (273-ФЗ от 29.12.2012 — дважды).
    return rows[0] if rows and len({r["status"] for r in rows}) == 1 else None


def ips_revision(client: httpx.Client, nd: str) -> str | None:
    """Последняя редакция из карточки: «10 - от 28.12.2024 № 547-ФЗ (изм.)».

    ponytail: у кодексов и часто изменяемых законов сервер собирает карточку минутами —
    короткий таймаут, и для них редакция не отслеживается (изменения видны по ленте опубликования).
    """
    try:
        page = _ips_get(client, f"docbody=&nd={nd}", timeout=REVISION_TIMEOUT)
    except httpx.HTTPError:
        return None
    m = re.search(r'<select name="doc_editions"[^>]*>([\s\S]*?)</select>', page)
    options = re.findall(r"<option[^>]*>([^<]+)</option>", m.group(1)) if m else []
    return _text(options[-1]) if options else None


def _ips_status(label: str) -> str | None:
    label = label.lower()
    if label.startswith("утратил"):
        return "repealed"
    return "active" if label.startswith("действует") else None


def _check_ips(client: httpx.Client, n: dict) -> dict | None:
    ref = _REF_RE.search(n.get("ips_ref") or n["title"])
    if not ref:
        return None
    day, number = ref.groups()
    found = ips_find(client, day, number)
    if not found and number.endswith("-1"):  # законы РФ 1990–1993: в ИПС «5485-I»
        found = ips_find(client, day, number[:-1] + "I")
    if not found:
        return None
    revision = ips_revision(client, found["nd"])
    ext = {"source": "ИПС pravo.gov.ru", "status": _ips_status(found["status"])}
    ext |= {"ips_nd": found["nd"], "ips_revision": revision}
    # Редакция «(не действ.)» датирована актом, отменившим документ, — годится, если даты нет.
    if revision and "не действ" in revision and (d := _DATE_RE.search(revision)):
        ext["since"] = d.group(0)
    return ext


# --- gostinfo.ru -----------------------------------------------------------------------------


def gost_search(client: httpx.Client, query: str) -> list[dict]:
    """Выдача каталога: [{id, designation, title, status}]."""
    r = client.get(f"{GOST_URL}/catalog/gostlist/", params={"searchString": query})
    r.raise_for_status()
    out = []
    for block in r.text.split('<div class="resultsInd">')[1:]:
        gid = re.search(r"/catalog/Details/\?id=([0-9a-f-]+)", block)
        h2 = re.search(r"<h2[^>]*>([\s\S]*?)</h2>", block)
        title = re.search(r'id="aDocIntro[0-9a-f-]+">([^<]+)<', block)
        status = re.search(r"Статус:\s*</strong>\s*<strong[^>]*>([^<]+)<", block)
        if gid and h2 and status:
            out.append(
                {
                    "id": gid.group(1),
                    "designation": _text(h2.group(1)),
                    "title": _text(title.group(1)) if title else "",
                    "status": _text(status.group(1)),
                }
            )
    return out


def gost_expiry(client: httpx.Client, gid: str) -> str | None:
    r = client.get(f"{GOST_URL}/catalog/Details/", params={"id": gid})
    r.raise_for_status()
    m = re.search(r"Дата огр\. срока действия:? (\d{2}\.\d{2}\.\d{4})", _text(r.text))
    return m.group(1) if m else None


def _gost_status(label: str) -> str | None:
    if label.startswith("Действует"):
        return "active"
    return "repealed" if label.startswith(("Заменен", "Отменен")) else None


def _designation(text: str | None) -> str | None:
    m = _GOST_RE.search(text or "")
    return text[: m.end()] if m and m.start() == 0 else None


def _gost_exact(client: httpx.Client, designation: str) -> dict | None:
    number = _GOST_RE.search(designation).group(1)
    hits = [h for h in gost_search(client, number) if h["designation"] == designation]
    return hits[0] if len(hits) == 1 else None


def _gost_successor(client: httpx.Client, designation: str) -> dict | None:
    """Действующий стандарт с тем же номером и другим годом (7.0.8-2013 → 7.0.8-2025)."""
    m = _GOST_RE.search(designation)
    base = m.group(1)[: -len(m.group(2)) - 1]
    same = re.compile(rf"{re.escape(designation[: m.start(1)] + base)}-\d{{2,4}}$")
    live = [
        h
        for h in gost_search(client, base)
        if same.match(h["designation"])
        and h["designation"] != designation
        and _gost_status(h["status"]) == "active"
    ]
    return max(live, key=lambda h: _year(h["designation"])) if live else None


def _year(designation: str) -> int:
    """Год стандарта числом: «-98» → 1998, «-2025» → 2025 (строки сравнивать нельзя: «99» > «2025»)."""
    y = int(_GOST_RE.search(designation).group(2))
    return y if y > 99 else y + (1900 if y > 50 else 2000)


def _check_gost(client: httpx.Client, n: dict) -> dict | None:
    designation = _designation(n["title"])
    hit = _gost_exact(client, designation) if designation else None
    if not hit:
        return None
    ext = {
        "source": "каталога gostinfo.ru",
        "status": _gost_status(hit["status"]),
        "since_exact": True,
    }
    if ext["status"] != "repealed":
        return ext
    ext["since"] = gost_expiry(client, hit["id"])
    # Замена тоже могла устареть (6.30-2003 → 7.0.97-2016 → 7.0.97-2025).
    olds = replacements(n)
    if len(olds) > 1:  # замена из нескольких актов — решает юрист
        return ext
    replacement = _designation(olds[0]) if olds else None
    current = _gost_exact(client, replacement) if replacement else None
    if not current or _gost_status(current["status"]) != "active":
        succ = _gost_successor(client, replacement or designation)
        if succ:
            ext["replaced_by"] = f"{succ['designation']} «{succ['title']}»"
            ext["new_norm"] = _gost_record(succ)
    return ext


def _gost_record(hit: dict) -> dict:
    """Запись справочника о действующем стандарте-замене, чтобы replaced_by совпадал с title."""
    designation = hit["designation"]
    number = _GOST_RE.search(designation).group(1)
    return {
        "id": f"gost-{'iso-' if 'ИСО' in designation else ''}{number}",
        "title": f"{designation} «{hit['title']}»",
        "status": "active",
        "patterns": [re.escape(designation).replace(r"\ ", r"\s*")],
        "verified": False,
        "source": "добавлен автосверкой как действующая замена; нормы (facts) не заполнены",
    }


# --- сверка ----------------------------------------------------------------------------------


def _apply(n: dict, ext: dict) -> Result | None:
    """Внести в запись n сведения источника. Result — если изменилось то, что должен увидеть юрист."""
    changes, notes = {}, []
    status = ext.get("status")
    compatible = status == n["status"] or (status == "active" and n["status"] == "partially")
    if status and not compatible:
        changes["status"] = status
        notes.append(f"по данным {ext['source']} — {_LABEL[status]}")
    since = ext.get("since")
    # Точная дата (ГОСТ) поправляет справочник, приблизительная (ИПС) — только заполняет пустую.
    better = not n.get("since") or (ext.get("since_exact") and since != n["since"])
    if since and changes.get("status", n["status"]) == "repealed" and better:
        changes["since"] = since
        if "status" not in changes:
            notes.append(f"дата утраты силы: {since}")
    if ext.get("replaced_by") and ext["replaced_by"] != n.get("replaced_by"):
        changes["replaced_by"] = ext["replaced_by"]
        notes.append(f"замена: {ext['replaced_by']}")
    old_rev, new_rev = n.get("ips_revision"), ext.get("ips_revision")
    if old_rev and new_rev and old_rev != new_rev:
        notes.append(f"новая редакция ({new_rev}) — сверьте нормы")
    # Служебные поля обновляются молча: первая запись редакции — точка отсчёта, а не новость.
    n.update({k: ext[k] for k in ("ips_nd", "ips_revision") if ext.get(k)})
    if not notes:  # каждое изменение поля даёт пояснение, так что changes здесь пуст
        return None
    note = "; ".join(notes)
    n.update(changes)
    n |= {"verified": False, "sync_note": note}
    return Result(norm_id=n["id"], changes=changes, note=note)


def _follow_replacements(norms: list[dict], already_noted: dict[str, Result]) -> list[Result]:
    """Если замена сама утратила силу и в справочнике есть её замена — перенести ссылку.

    already_noted — результаты этого прогона по id: пояснение дописывается к ним, а не дублируется.
    """
    by_title = {n["title"]: n for n in norms}
    out = []
    for n in norms:
        if not isinstance(n.get("replaced_by"), str):  # нет замены или замена из нескольких актов
            continue
        target, hops = by_title.get(n["replaced_by"]), 0
        while target and target["status"] == "repealed" and target.get("replaced_by") and hops < 5:
            n["replaced_by"], hops = target["replaced_by"], hops + 1
            target = by_title.get(n["replaced_by"])
        if hops:
            note = f"замена утратила силу, ссылка перенесена на: {n['replaced_by']}"
            if prev := already_noted.get(n["id"]):
                prev["note"] = f"{prev['note']}; {note}"
                prev["changes"]["replaced_by"] = n["replaced_by"]
                n["sync_note"] = prev["note"]
            else:
                n |= {"verified": False, "sync_note": note}
                out.append(
                    Result(norm_id=n["id"], changes={"replaced_by": n["replaced_by"]}, note=note)
                )
    return out


def sync(norms: list[dict], client: httpx.Client) -> tuple[list[dict], Report]:
    """Сверить и обновить копию справочника. Возвращает новые записи и отчёт."""
    records = [{k: v for k, v in n.items() if not k.startswith("_")} for n in norms]

    def fetch(n: dict) -> dict | None:
        try:
            return _check_gost(client, n) if n["id"].startswith("gost-") else _check_ips(client, n)
        except httpx.HTTPError:  # сайт недоступен — запись остаётся несверенной
            return None

    with ThreadPoolExecutor(WORKERS) as pool:
        found = list(pool.map(fetch, records))
    changed = [r for n, ext in zip(records, found, strict=True) if ext and (r := _apply(n, ext))]
    unchecked = [
        n["id"] for n, ext in zip(records, found, strict=True) if not ext or not ext.get("status")
    ]
    checked = len(records) - len(unchecked)
    # Новые акты-замены — в справочник, иначе замена не совпала бы ни с одним title.
    known = {n["title"] for n in records} | {n["id"] for n in records}
    for ext in found:
        new = (ext or {}).get("new_norm")
        if new and new["title"] not in known and new["id"] not in known:
            records.append(new)
            known |= {new["title"], new["id"]}
    changed += _follow_replacements(records, {r["norm_id"]: r for r in changed})
    report = Report(checked=checked, changed=changed, unchecked=unchecked)
    return records, report


def run(norms: list[dict]) -> tuple[list[dict], Report]:
    """Сверка с настройками клиента по умолчанию (для админки и CLI)."""
    headers = {"User-Agent": "Mozilla/5.0 (compatible; gosdoc-norms-sync)"}
    with httpx.Client(timeout=40, headers=headers, follow_redirects=True) as client:
        return sync(norms, client)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument("--apply", action="store_true", help="записать изменения в БД")
    mode.add_argument("--write-json", action="store_true", help="сверить и обновить norms.json")
    args = parser.parse_args()
    from app.db import init_db
    from app.services import norms_store

    if args.apply:  # как при старте приложения: таблицы и стартовый снимок
        init_db()
        norms_store.seed()
    # Снимок сверяется сам по себе, без БД: иначе в git уехали бы правки конкретной инсталляции.
    before = norms_store._from_file() if args.write_json else norms_store.load_norms()
    records, report = run(before)
    print(f"Сверено: {report['checked']}, изменено: {len(report['changed'])}")
    for r in report["changed"]:
        print(f"- {r['norm_id']}: {r['note']}")
    if report["unchecked"]:
        print(f"Не сверено (нет в источниках или недоступны): {', '.join(report['unchecked'])}")
    if args.apply and report["checked"]:
        norms_store.save(records, before)
        print("Изменения записаны в БД.")
    if args.write_json and report["checked"]:
        norms_store.write_file(records)
        print(f"Записано в {norms_store.DATA / 'norms.json'}.")
    return 1 if report["changed"] else 0


if __name__ == "__main__":
    sys.exit(main())
