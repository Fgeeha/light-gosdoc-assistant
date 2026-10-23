"""Проверка документа на ссылки на утратившие силу и изменённые нормативные акты.

Поиск ссылок детерминированный (по справочной базе), модель только переписывает
текст по найденным фактам — так она не «придумывает» статусы законов.
"""

import json
import re
from functools import lru_cache
from pathlib import Path

DATA = Path(__file__).resolve().parent.parent / "data"

# Ссылки, похожие на НПА, — чтобы честно сообщить «нет в базе».
_GENERIC_REFS = re.compile(
    r"(?<![\w-])\d{1,4}-ФЗ"
    r"|ГОСТ(?:\s*Р)?\s*(?:ИСО\s*)?[\d.]+-\d{2,4}"
    r"|СанПиН\s*[\d./]+-\d{2}"
    r"|СП\s*[\d.]+-\d{2}"
)


@lru_cache
def load_norms() -> list[dict]:
    norms = json.loads((DATA / "norms.json").read_text(encoding="utf-8"))["norms"]
    for n in norms:
        n["_re"] = re.compile("|".join(f"(?:{p})" for p in n["patterns"]), re.IGNORECASE)
    return norms


def find_references(text: str) -> list[dict]:
    """Находки по тексту в порядке появления: известные акты и неизвестные ссылки."""
    findings, covered = [], []
    by_title = {n["title"]: n for n in load_norms()}
    for n in load_norms():
        matches = list(n["_re"].finditer(text))
        if not matches:
            continue
        covered += [m.span() for m in matches]
        findings.append(
            {
                "norm_id": n["id"],
                "status": n["status"],
                "title": n["title"],
                "quote": matches[0].group(0),
                "position": matches[0].start(),
                "occurrences": len(matches),
                "since": n.get("since"),
                "replaced_by": n.get("replaced_by"),
                "comment": n.get("comment"),
                # Для отменённого акта — нормы того, что его заменил.
                "facts": n.get("facts") or by_title.get(n.get("replaced_by"), {}).get("facts", []),
            }
        )
    seen = set()
    for m in _GENERIC_REFS.finditer(text):
        ref = re.sub(r"\s+", " ", m.group(0))
        overlaps = any(s <= m.start() < e or m.start() <= s < m.end() for s, e in covered)
        if overlaps or ref in seen:
            continue
        seen.add(ref)
        findings.append(
            {
                "norm_id": None,
                "status": "unknown",
                "title": ref,
                "quote": m.group(0),
                "position": m.start(),
                "occurrences": 1,
                "facts": [],
            }
        )
    return sorted(findings, key=lambda f: f["position"])


def needs_rewrite(findings: list[dict]) -> bool:
    return any(f["status"] in ("repealed", "partially") or f["facts"] for f in findings)


SYSTEM_PROMPT = """Вы — юрист-эксперт по делопроизводству в органах государственной власти Российской Федерации.
Ваша задача — актуализировать документ: заменить ссылки на утратившие силу нормативные акты действующими и привести положения документа в соответствие с указанными нормами.

Правила:
1. Опирайтесь ТОЛЬКО на справку о нормативных актах ниже. Не добавляйте других законов, номеров и дат.
2. Меняйте только то, что противоречит справке; остальной текст, нумерацию пунктов и стиль сохраняйте дословно.
3. Если пункт противоречит конкретной норме из справки (сроки, порядок), перепишите его по этой норме.
4. Выведите только полный исправленный текст документа, без пояснений, заголовков «Исправленный текст» и Markdown-разметки."""


def _brief(findings: list[dict]) -> str:
    lines = []
    for f in findings:
        if f["status"] == "unknown":
            continue
        if f["status"] == "repealed":
            line = (
                f"- {f['title']} — УТРАТИЛ СИЛУ (с {f['since']}). Заменить на: {f['replaced_by']}."
            )
        elif f["status"] == "partially":
            line = f"- {f['title']} — ПРИМЕНЯЕТСЯ ОГРАНИЧЕННО (с {f['since']}). Ссылаться на: {f['replaced_by']}."
        else:
            line = f"- {f['title']} — действует."
        if f.get("comment"):
            line += f" {f['comment']}"
        lines.append(line)
        lines += [f"  • {fact}" for fact in f["facts"]]
    return "\n".join(lines)


def build_messages(text: str, findings: list[dict]) -> list[dict]:
    return [
        {"role": "system", "content": SYSTEM_PROMPT},
        {
            "role": "user",
            "content": f"Справка о нормативных актах:\n{_brief(findings)}\n\nДокумент:\n<<<\n{text}\n>>>",
        },
    ]
