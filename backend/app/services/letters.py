"""Подготовка проекта ответа на обращение или запрос."""

from app.llm import Message
from app.schemas import LetterIn
from app.services.normcheck import find_references, load_norms

# Законы об обращениях, доступе к информации и архивном деле — нужны почти в любом ответе.
ALWAYS_CITED_NORMS = frozenset({"fz-59", "fz-8-2009", "fz-125"})

SYSTEM_PROMPT = """Вы — опытный специалист по делопроизводству органа государственной власти (местного самоуправления) Российской Федерации.
Подготовьте проект официального ответа на входящее обращение.

Требования:
1. Официально-деловой стиль, вежливо, без канцелярских штампов и воды. Ответ по существу каждого поставленного вопроса.
2. Структура: адресат (кому, адрес или e-mail из обращения) → обращение «Уважаемый(ая) …!» → ссылка на входящее обращение (дата) → ответ по существу по пунктам → при необходимости разъяснение порядка обжалования или дальнейших действий → подпись (должность, инициалы и фамилия) → строка «Исп.: [заполнить: ФИО, телефон]».
3. Ссылайтесь только на нормативные акты из списка ниже и только если это уместно. Не придумывайте номера, даты, суммы, телефоны и адреса.
4. Если для ответа не хватает сведений — поставьте метку [заполнить: что именно].
5. Выведите только текст письма, без Markdown-разметки и комментариев."""


def _norms_brief(incoming: str) -> str:
    mentioned = {f["norm_id"] for f in find_references(incoming)}
    lines = []
    for n in load_norms():
        if n["status"] != "active":
            continue
        if n["id"] in mentioned or n["id"] in ALWAYS_CITED_NORMS:
            lines.append(f"- {n['title']}")
            lines += [f"  • {fact}" for fact in n.get("facts", [])]
    return "\n".join(lines)


def build_messages(letter: LetterIn) -> list[Message]:
    user = (
        f"Действующие нормативные акты:\n{_norms_brief(letter.incoming)}\n\n"
        f"Орган, от имени которого готовится ответ: {letter.organization or '[заполнить]'}\n"
        f"Подписант: {letter.signer or '[заполнить]'}\n"
        f"Позиция и сведения для ответа: {letter.instructions or 'нет, подготовить общий ответ'}\n\n"
        f"Входящее обращение:\n<<<\n{letter.incoming}\n>>>"
    )
    return [{"role": "system", "content": SYSTEM_PROMPT}, {"role": "user", "content": user}]
