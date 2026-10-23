"""Сравнение моделей на демо-сценариях теми же промптами, что и в приложении.

Запуск: REPEATS=3 uv run python scripts/bench.py qwen3:14b gemma3:12b ...
Результаты (среднее по прогонам): bench-results/<дата>/summary.md, results.json, тексты ответов.
"""

import asyncio
import difflib
import json
import re
import sys
import time
from datetime import datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import os  # noqa: E402

os.environ.setdefault("SECRET_KEY", "bench" * 8)
os.environ.setdefault("DATABASE_URL", "sqlite://")

from app import llm  # noqa: E402
from app.services import letters, normcheck, tables  # noqa: E402

EXAMPLES = normcheck.DATA / "examples"

# Эталонные требования к актуализированным документам.
CHECK_GOLD = {
    "appeals-regulation": {
        "absent": ["2534-VII", "24-ФЗ", "6.30-2003", "РСФСР"],
        "present": ["59-ФЗ", "149-ФЗ", "7.0.97-2016", "Российской Федерации об административных"],
        "facts": [r"тр[её]х дней|3 дн", r"30 дней"],
    },
    "pd-protection": {
        "absent": ["24-ФЗ", "1-ФЗ", "1340-03", "цифровой подпис"],
        "present": ["152-ФЗ", "149-ФЗ", "63-ФЗ", "3685-21"],
        "facts": [r"24 час", r"отдельн"],
    },
    "procurement-tz": {
        "absent": ["94-ФЗ", "128-ФЗ", "51141-98", "6.30-2003", "ЭЦП"],
        "present": ["44-ФЗ", "99-ФЗ", "248-ФЗ", "7.0.8-2013", "7.0.97-2016"],
        "facts": [],
    },
}
LETTER_GOLD = {
    "road-repair": ["Сидоров", r"II квартал|втор\w+ квартал", "водоотвод", r"Петров|Мари"],
    "info-request": ["Кравцова", "Бюджет", "ЕИС|единой информационной системе", "сквер"],
    "archive-certificate": ["Лебедева", "паспорт", "трудов", "1991", r"Ковал[её]в|Серге"],
}
CJK = re.compile(r"[぀-ヿ㐀-鿿가-힯]")
LONG_NUMBER = re.compile(r"\d[\d\-\s()]{8,}\d")


def _norm(s: str) -> str:
    return re.sub(r"\s+", " ", s.replace("ё", "е").replace("Ё", "Е")).strip()


async def timed_stream(messages: list[dict], model: str) -> tuple[str, float, float]:
    start, first, parts = time.perf_counter(), None, []
    async for chunk in llm.stream_chat(messages, model):
        first = first or time.perf_counter()
        parts.append(chunk)
    end = time.perf_counter()
    return "".join(parts).strip(), (first or end) - start, end - start


def score_check(ex: dict, out: str) -> tuple[float, list[str]]:
    gold, fails, total = CHECK_GOLD[ex["id"]], [], 0
    for s in gold["absent"]:
        total += 1
        if s in out:
            fails.append(f"осталась ссылка «{s}»")
    for s in gold["present"]:
        total += 1
        if s not in out:
            fails.append(f"нет «{s}»")
    for pattern in gold["facts"]:
        total += 1
        if not re.search(pattern, out):
            fails.append(f"не применена норма /{pattern}/")
    total += 1
    ratio = difflib.SequenceMatcher(None, _norm(ex["text"]), _norm(out), autojunk=False).ratio()
    if ratio < 0.6:
        fails.append(f"текст переписан слишком сильно (сходство {ratio:.2f})")
    total += 1
    if CJK.search(out):
        fails.append("иероглифы в ответе")
    return 1 - len(fails) / total, fails


def score_letter(ex: dict, out: str) -> tuple[float, list[str]]:
    fails, checks = [], LETTER_GOLD[ex["id"]]
    for pattern in checks:
        if not re.search(pattern, out):
            fails.append(f"нет /{pattern}/")
    if "Уважаем" not in out:
        fails.append("нет обращения «Уважаемый…»")
    if "**" in out or re.search(r"^#", out, re.M):
        fails.append("Markdown-разметка")
    if CJK.search(out):
        fails.append("иероглифы в ответе")
    bad = [f["title"] for f in normcheck.find_references(out) if f["status"] != "active"]
    if bad:
        fails.append(f"сомнительные ссылки на НПА: {bad}")
    source = re.sub(r"\D", "", ex["incoming"])
    invented = [n for n in LONG_NUMBER.findall(out) if re.sub(r"\D", "", n) not in source]
    if invented:
        fails.append(f"выдуманные номера: {invented}")
    if not 500 <= len(out) <= 5000:
        fails.append(f"длина {len(out)} симв.")
    return 1 - len(fails) / (len(checks) + 6), fails


async def run_table(ex: dict, model: str) -> tuple[list[dict], float]:
    _, rows = tables.parse_table(ex["source"])
    start, result = time.perf_counter(), []
    for part in tables.chunks(rows):
        data = await llm.complete_json(
            tables.build_messages(part, ex["columns"], ex["instructions"]), model
        )
        result += tables.normalize_result(data, ex["columns"], len(part))
    return result, time.perf_counter() - start


def score_table(ex: dict, rows: list[dict]) -> tuple[float, list[str]]:
    fails, total = [], 0
    for i, (got, want) in enumerate(zip(rows, ex["expected"], strict=True), 1):
        for col, value in want.items():
            total += 1
            if _norm(got.get(col, "")) != _norm(value):
                fails.append(f"стр. {i} «{col}»: «{got.get(col)}» вместо «{value}»")
    return 1 - len(fails) / total, fails


async def bench_model(model: str, out_dir: Path) -> dict:
    load = lambda name: json.loads((EXAMPLES / f"{name}.json").read_text(encoding="utf-8"))  # noqa: E731
    res = {"model": model, "cases": []}
    t = time.perf_counter()
    await llm.complete_json([{"role": "user", "content": 'Ответь {"ok": true}'}], model)
    res["load_seconds"] = round(time.perf_counter() - t, 1)

    async def case(kind: str, ex: dict, coro):
        try:
            score, fails, out, ttft, total = await coro
        except Exception as e:  # модель сломалась — фиксируем и идём дальше
            score, fails, out, ttft, total = 0.0, [f"ошибка: {e}"], "", 0, 0
        safe = re.sub(r"\W+", "_", model)
        (out_dir / f"{safe}__{kind}__{ex['id']}.txt").write_text(out, encoding="utf-8")
        res["cases"].append(
            {
                "kind": kind,
                "id": ex["id"],
                "score": round(score, 3),
                "fails": fails,
                "ttft": round(ttft, 2),
                "seconds": round(total, 1),
                "chars": len(out),
            }
        )
        print(
            f"  {kind:6} {ex['id']:22} {score:5.0%} {total:6.1f}s  {'; '.join(fails)[:150]}",
            flush=True,
        )

    async def check(ex):
        findings = normcheck.find_references(ex["text"])
        out, ttft, total = await timed_stream(normcheck.build_messages(ex["text"], findings), model)
        return (*score_check(ex, out), out, ttft, total)

    async def letter(ex):
        msgs = letters.build_messages(
            ex["incoming"], ex["organization"], ex["signer"], ex["instructions"]
        )
        out, ttft, total = await timed_stream(msgs, model)
        return (*score_letter(ex, out), out, ttft, total)

    async def table(ex):
        rows, total = await run_table(ex, model)
        return (*score_table(ex, rows), json.dumps(rows, ensure_ascii=False, indent=1), 0, total)

    for ex in load("check"):
        await case("check", ex, check(ex))
    for ex in load("letters"):
        await case("letter", ex, letter(ex))
    for ex in load("tables"):
        await case("table", ex, table(ex))

    for kind in ("check", "letter", "table"):
        cs = [c for c in res["cases"] if c["kind"] == kind]
        res[kind] = round(sum(c["score"] for c in cs) / len(cs), 3)
        res[f"{kind}_seconds"] = round(sum(c["seconds"] for c in cs) / len(cs), 1)
    res["overall"] = round((res["check"] + res["letter"] + res["table"]) / 3, 3)
    stream = [c for c in res["cases"] if c["kind"] != "table" and c["seconds"]]
    res["chars_per_second"] = round(
        sum(c["chars"] for c in stream) / sum(c["seconds"] for c in stream)
    )
    return res


def summary(results: list[dict]) -> str:
    lines = [
        "| Модель | Итог | Проверка | Письмо | Таблицы | Проверка, с | Письмо, с | Таблица, с | Симв/с |",
        "|---|---|---|---|---|---|---|---|---|",
    ]
    for r in sorted(results, key=lambda r: -r["overall"]):
        lines.append(
            f"| {r['model']} | **{r['overall']:.0%}** | {r['check']:.0%} | {r['letter']:.0%} | "
            f"{r['table']:.0%} | {r['check_seconds']:.1f} | {r['letter_seconds']:.1f} | "
            f"{r['table_seconds']:.1f} | {r['chars_per_second']:.0f} |"
        )
    return "\n".join(lines)


def average(runs: list[dict]) -> dict:
    keys = [
        "check",
        "letter",
        "table",
        "overall",
        "check_seconds",
        "letter_seconds",
        "table_seconds",
        "chars_per_second",
    ]
    avg = {k: round(sum(r[k] for r in runs) / len(runs), 3) for k in keys}
    return {"model": runs[0]["model"], "runs": len(runs), **avg, "details": runs}


async def main(models: list[str], repeats: int) -> None:
    out_dir = Path("bench-results") / datetime.now().strftime("%Y%m%d-%H%M%S")
    results = []
    for model in models:
        runs = []
        for i in range(repeats):
            print(f"== {model}, прогон {i + 1}/{repeats}", flush=True)
            run_dir = out_dir / f"run{i + 1}"
            run_dir.mkdir(parents=True, exist_ok=True)
            try:
                runs.append(await bench_model(model, run_dir))
            except Exception as e:
                print(f"  пропущена: {e}", flush=True)
                break
        if runs:
            results.append(average(runs))
        (out_dir / "results.json").write_text(json.dumps(results, ensure_ascii=False, indent=1))
    table = summary(results)
    (out_dir / "summary.md").write_text(table + "\n", encoding="utf-8")
    print(f"\n{table}\n\nРезультаты: {out_dir}")


if __name__ == "__main__":
    if len(sys.argv) < 2:
        sys.exit("Укажите модели: scripts/bench.py qwen3:14b gemma3:12b")
    asyncio.run(main(sys.argv[1:], int(os.environ.get("REPEATS", "3"))))
