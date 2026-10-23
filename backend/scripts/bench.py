"""Сравнение моделей на демо-сценариях теми же промптами, что и в приложении.

Запуск: REPEATS=3 uv run python scripts/bench.py qwen3:14b gemma3:12b ...
Результаты (среднее по прогонам): bench-results/<дата>/summary.md, results.json, тексты ответов.
В шапке summary.md — коммит и LLM_EXTRA_BODY, чтобы не смешивать прогоны разных версий.
"""

import asyncio
import difflib
import json
import logging
import re
import subprocess
import sys
import time
from collections.abc import Awaitable
from datetime import datetime
from pathlib import Path

import httpx

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import os  # noqa: E402

os.environ.setdefault("SECRET_KEY", "bench" * 8)
os.environ.setdefault("DATABASE_URL", "sqlite://")

from app import llm  # noqa: E402
from app.config import get_settings  # noqa: E402
from app.schemas import LetterIn  # noqa: E402
from app.services import letters, normcheck, tables  # noqa: E402

EXAMPLES = normcheck.DATA / "examples"
logger = logging.getLogger("bench")
# (оценка, замечания, текст ответа, время до первого токена, полное время)
CaseResult = tuple[float, list[str], str, float, float]

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


async def timed_stream(messages: list[llm.Message], model: str) -> tuple[str, float, float]:
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
    res["vram_gb"] = await ollama_vram_gb(model)

    async def case(kind: str, ex: dict, coro: Awaitable[CaseResult]) -> None:
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
        logger.info(
            "  %-6s %-22s %5.0f%% %6.1fs  %s",
            kind,
            ex["id"],
            score * 100,
            total,
            "; ".join(fails)[:150],
        )

    async def check(ex: dict) -> CaseResult:
        findings = normcheck.find_references(ex["text"])
        out, ttft, total = await timed_stream(normcheck.build_messages(ex["text"], findings), model)
        return (*score_check(ex, out), out, ttft, total)

    async def letter(ex: dict) -> CaseResult:
        fields = ("incoming", "organization", "signer", "instructions")
        msgs = letters.build_messages(LetterIn(**{k: ex[k] for k in fields}))
        out, ttft, total = await timed_stream(msgs, model)
        return (*score_letter(ex, out), out, ttft, total)

    async def table(ex: dict) -> CaseResult:
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


async def ollama_vram_gb(model: str) -> float | None:
    """Видеопамять, занятая моделью, по /api/ps Ollama; для других серверов — None."""
    base = get_settings().llm_base_url.rstrip("/").removesuffix("/v1")
    try:
        async with httpx.AsyncClient(timeout=5) as client:
            r = await client.get(f"{base}/api/ps")
            r.raise_for_status()
    except httpx.HTTPError:
        return None
    sizes = [m.get("size_vram", 0) for m in r.json().get("models", []) if m.get("name") == model]
    return round(sizes[0] / 1024**3, 1) if sizes else None


def run_info(repeats: int) -> str:
    try:
        commit = subprocess.run(
            ["git", "describe", "--always", "--dirty"], capture_output=True, text=True, check=True
        ).stdout.strip()
    except (OSError, subprocess.CalledProcessError):
        commit = "неизвестен"
    extra = json.dumps(get_settings().llm_extra_body, ensure_ascii=False)
    return (
        f"Коммит: {commit}; LLM_EXTRA_BODY: {extra}; прогонов на модель: {repeats}; "
        f"дата: {datetime.now():%d.%m.%Y %H:%M}"
    )


def summary(results: list[dict]) -> str:
    lines = [
        "| Модель | Итог | Проверка | Письмо | Таблицы | Проверка, с | Письмо, с | Таблица, с "
        "| Симв/с | VRAM, ГБ |",
        "|---|---|---|---|---|---|---|---|---|---|",
    ]
    for r in sorted(results, key=lambda r: -r["overall"]):
        lines.append(
            f"| {r['model']} | **{r['overall']:.0%}** | {r['check']:.0%} | {r['letter']:.0%} | "
            f"{r['table']:.0%} | {r['check_seconds']:.1f} | {r['letter_seconds']:.1f} | "
            f"{r['table_seconds']:.1f} | {r['chars_per_second']:.0f} | {r['vram_gb'] or '—'} |"
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
    vram = [r["vram_gb"] for r in runs if r["vram_gb"]]
    return {
        "model": runs[0]["model"],
        "runs": len(runs),
        **avg,
        "vram_gb": max(vram) if vram else None,
        "details": runs,
    }


async def main(models: list[str], repeats: int) -> None:
    out_dir = Path("bench-results") / datetime.now().strftime("%Y%m%d-%H%M%S")
    results = []
    for model in models:
        runs = []
        for i in range(repeats):
            logger.info("== %s, прогон %d/%d", model, i + 1, repeats)
            run_dir = out_dir / f"run{i + 1}"
            run_dir.mkdir(parents=True, exist_ok=True)
            try:
                runs.append(await bench_model(model, run_dir))
            except Exception as e:
                logger.warning("  пропущена: %s", e)
                break
        if runs:
            results.append(average(runs))
        dump = {"info": run_info(repeats), "results": results}
        (out_dir / "results.json").write_text(json.dumps(dump, ensure_ascii=False, indent=1))
    report = f"{run_info(repeats)}\n\n{summary(results)}\n"
    (out_dir / "summary.md").write_text(report, encoding="utf-8")
    logger.info("\n%s\nРезультаты: %s", report, out_dir)


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(message)s", stream=sys.stdout)
    if len(sys.argv) < 2:
        sys.exit("Укажите модели: scripts/bench.py qwen3:14b gemma3:12b")
    asyncio.run(main(sys.argv[1:], int(os.environ.get("REPEATS", "3"))))
