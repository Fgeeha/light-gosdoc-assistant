"""Проверка документа на ссылки на утратившие силу и изменённые нормативные акты.

Поиск ссылок детерминированный (по справочной базе), модель только переписывает
текст по найденным фактам — так она не «придумывает» статусы законов.
"""

import re
from typing import Literal, TypedDict

from app.llm import Message
from app.services.norms_store import DATA, load_norms  # re-export: справочник в norms_store

__all__ = [
    "DATA",
    "Finding",
    "build_messages",
    "find_references",
    "load_norms",
    "needs_rewrite",
    "replacements",
]

# Ссылки, похожие на НПА, — чтобы честно сообщить «нет в базе».
_GENERIC_REFS = re.compile(
    r"(?<![\w-])\d{1,4}-ФЗ"
    r"|ГОСТ(?:\s*Р)?\s*(?:ИСО\s*)?[\d.]+-\d{2,4}"
    r"|СанПиН\s*[\d./]+-\d{2}"
    r"|СП\s*[\d.]+-\d{2}"
)

Status = Literal["active", "repealed", "partially", "unknown"]


class Finding(TypedDict):
    norm_id: str | None
    status: Status
    title: str
    quote: str
    position: int
    occurrences: int
    since: str | None
    replaced_by: str | None
    comment: str | None
    facts: list[str]


def replacements(norm: dict) -> list[str]:
    """Наименования актов-замен: replaced_by — строка или список (замена из нескольких актов)."""
    value = norm.get("replaced_by") or []
    return [value] if isinstance(value, str) else list(value)


def find_references(text: str) -> list[Finding]:
    """Находки по тексту в порядке появления: известные акты и неизвестные ссылки."""
    findings: list[Finding] = []
    covered: list[tuple[int, int]] = []
    by_title = {n["title"]: n for n in load_norms()}
    for n in load_norms():
        matches = list(n["_re"].finditer(text))
        if not matches:
            continue
        covered += [m.span() for m in matches]
        findings.append(
            Finding(
                norm_id=n["id"],
                status=n["status"],
                title=n["title"],
                quote=matches[0].group(0),
                position=matches[0].start(),
                occurrences=len(matches),
                since=n.get("since"),
                replaced_by="; ".join(replacements(n)) or None,
                comment=n.get("comment"),
                # Для отменённого акта — нормы того, что его заменил.
                facts=n.get("facts")
                or [x for t in replacements(n) for x in by_title.get(t, {}).get("facts", [])],
            )
        )
    seen = set()
    for m in _GENERIC_REFS.finditer(text):
        ref = re.sub(r"\s+", " ", m.group(0))
        overlaps = any(s <= m.start() < e or m.start() <= s < m.end() for s, e in covered)
        if overlaps or ref in seen:
            continue
        seen.add(ref)
        findings.append(
            Finding(
                norm_id=None,
                status="unknown",
                title=ref,
                quote=m.group(0),
                position=m.start(),
                occurrences=1,
                since=None,
                replaced_by=None,
                comment=None,
                facts=[],
            )
        )
    return sorted(findings, key=lambda f: f["position"])


def needs_rewrite(findings: list[Finding]) -> bool:
    return any(f["status"] in ("repealed", "partially") or f["facts"] for f in findings)


SYSTEM_PROMPT = """Вы — юрист-эксперт по делопроизводству в органах государственной власти Российской Федерации.
Ваша задача — актуализировать документ: заменить ссылки на утратившие силу нормативные акты действующими и привести положения документа в соответствие с указанными нормами.

Правила:
1. Опирайтесь ТОЛЬКО на справку о нормативных актах ниже. Не добавляйте других законов, номеров и дат.
2. Меняйте только то, что противоречит справке; остальной текст, нумерацию пунктов и стиль сохраняйте дословно.
3. Если пункт противоречит конкретной норме из справки (сроки, порядок), перепишите его по этой норме.
4. Выведите только полный исправленный текст документа, без пояснений, заголовков «Исправленный текст» и Markdown-разметки."""


def _brief(findings: list[Finding]) -> str:
    lines = []
    for f in findings:
        if f["status"] == "unknown":
            continue
        if f["status"] in ("repealed", "partially"):
            repealed = f["status"] == "repealed"
            line = f"- {f['title']} — {'УТРАТИЛ СИЛУ' if repealed else 'ПРИМЕНЯЕТСЯ ОГРАНИЧЕННО'}"
            # since/replaced_by могут быть пустыми после правки статуса из админки.
            if f["since"]:
                line += f" (с {f['since']})"
            if f["replaced_by"]:
                line += f". {'Заменить на' if repealed else 'Ссылаться на'}: {f['replaced_by']}."
            else:
                line += ". Замена в справочнике не указана."
        else:
            line = f"- {f['title']} — действует."
        if f.get("comment"):
            line += f" {f['comment']}"
        lines.append(line)
        lines += [f"  • {fact}" for fact in f["facts"]]
    return "\n".join(lines)


def build_messages(text: str, findings: list[Finding]) -> list[Message]:
    return [
        {"role": "system", "content": SYSTEM_PROMPT},
        {
            "role": "user",
            "content": f"Справка о нормативных актах:\n{_brief(findings)}\n\nДокумент:\n<<<\n{text}\n>>>",
        },
    ]
