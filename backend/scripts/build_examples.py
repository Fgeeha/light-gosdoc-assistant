"""Разбирает исходные документы образцов и записывает текст в examples/*.json.

Исходники лежат рядом: examples/docs/<раздел>/<id>.docx|.csv|.tsv,
эталонные редакции проверки — examples/docs/check/<id>.expected.docx.

Запуск: uv run python scripts/build_examples.py
"""

import json
import logging
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.services.files import extract_text  # noqa: E402

EXAMPLES = Path(__file__).resolve().parent.parent / "app" / "data" / "examples"
DOCS = EXAMPLES / "docs"
# раздел → [(суффикс файла, поле JSON)]
SOURCES: dict[str, list[tuple[str, str]]] = {
    "check": [("", "text"), (".expected", "expected")],
    "letters": [("", "incoming")],
    "tables": [("", "source")],
}
logger = logging.getLogger("build_examples")


def _source_file(kind: str, item_id: str, suffix: str) -> Path:
    matches = [
        m
        for m in (DOCS / kind).glob(f"{item_id}.*")
        if m.name[len(item_id) : -len(m.suffix)] == suffix
    ]
    if len(matches) != 1:
        raise FileNotFoundError(f"{kind}/{item_id}{suffix}.*: найдено {len(matches)} файлов")
    return matches[0]


def build(write: bool = True) -> list[str]:
    """Возвращает список полей, которые расходились с исходными файлами."""
    changed = []
    for kind, fields in SOURCES.items():
        path = EXAMPLES / f"{kind}.json"
        items = json.loads(path.read_text(encoding="utf-8"))
        for item in items:
            for suffix, field in fields:
                src = _source_file(kind, item["id"], suffix)
                text = extract_text(src.name, src.read_bytes())
                if item.get(field) != text:
                    changed.append(f"{kind}/{item['id']}.{field}")
                    item[field] = text
        if write:
            dump = json.dumps(items, ensure_ascii=False, indent=2) + "\n"
            path.write_text(dump, encoding="utf-8")
    return changed


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(message)s")
    updated = build()
    logger.info("Обновлено полей: %d %s", len(updated), updated or "")
