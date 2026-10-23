"""Перенос данных из исходной таблицы в целевую форму."""

import csv
import io
import json

from app.llm import Message

MAX_ROWS = 500
CHUNK_ROWS = 15

SYSTEM_PROMPT = """Вы — аккуратный оператор ввода данных в государственном учреждении.
Перенесите записи исходной таблицы в целевую форму с заданными колонками.

Правила:
1. Каждой исходной записи соответствует ровно одна запись результата, в том же порядке.
2. Используйте только данные из исходной записи. Не выдумывайте значений; если данных нет — пустая строка.
3. Строго соблюдайте форматы из инструкции.
4. Ответ — только JSON-объект вида {"rows": [{"<колонка>": "<значение>", ...}, ...]} со всеми целевыми колонками, значения — строки."""


def parse_table(text: str) -> tuple[list[str], list[dict]]:
    """CSV/TSV или текст, скопированный из Excel. Возвращает заголовок и строки."""
    text = text.strip("\ufeff\n\r ")
    if not text:
        raise ValueError("Исходная таблица пуста")
    sample = text[:4096]
    try:
        dialect = csv.Sniffer().sniff(sample, delimiters="\t;,|")
    except csv.Error:
        # Sniffer не справляется с рваными строками — берём самый частый разделитель заголовка.
        first = sample.split("\n", 1)[0]
        dialect = csv.excel
        dialect_args = {"delimiter": max("\t;,|", key=first.count)}
    else:
        dialect_args = {}
    reader = csv.reader(io.StringIO(text), dialect, **dialect_args)
    header = [h.strip() for h in next(reader)]
    if not all(header) or len(set(header)) != len(header):
        raise ValueError("Заголовки колонок должны быть непустыми и не повторяться")
    raw = [r for r in reader if any(c.strip() for c in r)]
    if any(len(r) > len(header) for r in raw):
        raise ValueError("В некоторых строках больше значений, чем колонок в заголовке")
    rows = [dict(zip(header, (c.strip() for c in r), strict=False)) for r in raw]
    if not rows:
        raise ValueError("В таблице нет строк с данными")
    if len(rows) > MAX_ROWS:
        raise ValueError(f"Не более {MAX_ROWS} строк за один раз")
    return header, rows


def chunks(rows: list[dict]) -> list[list[dict]]:
    return [rows[i : i + CHUNK_ROWS] for i in range(0, len(rows), CHUNK_ROWS)]


def build_messages(rows: list[dict], columns: list[str], instructions: str) -> list[Message]:
    user = (
        f"Целевые колонки: {json.dumps(columns, ensure_ascii=False)}\n"
        f"Инструкция: {instructions or 'нет'}\n\n"
        f"Исходные записи ({len(rows)} шт.):\n{json.dumps(rows, ensure_ascii=False, indent=1)}"
    )
    return [{"role": "system", "content": SYSTEM_PROMPT}, {"role": "user", "content": user}]


def normalize_result(data: dict, columns: list[str], expected: int) -> list[dict]:
    """Приводит ответ модели к ровно `expected` строкам с нужными колонками."""
    rows = data.get("rows") if isinstance(data, dict) else None
    if not isinstance(rows, list):
        raise ValueError("В ответе модели нет массива rows")
    out = []
    for r in rows[:expected]:
        r = r if isinstance(r, dict) else {}
        out.append({c: "" if r.get(c) is None else str(r.get(c)).strip() for c in columns})
    if len(out) != expected:
        raise ValueError(f"Модель вернула {len(out)} строк вместо {expected}")
    return out
