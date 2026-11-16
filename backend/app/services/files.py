"""Извлечение текста из загруженных файлов и выгрузка в .docx."""

import io
import re
import zipfile

from docx import Document
from docx.shared import Pt
from pypdf import PdfReader

MAX_BYTES = 10 * 1024 * 1024
# Защита от «бомб»: .docx — это zip, 10 МБ легко распаковываются в гигабайты.
# ponytail: размеры берутся из заголовков архива; подделанный заголовок обходит проверку —
# тогда остаётся лимит памяти контейнера.
MAX_UNPACKED_BYTES = 50 * 1024 * 1024
MAX_PDF_PAGES = 300
MAX_TEXT_CHARS = 300_000  # больше не принимает ни один инструмент (TableIn.source)
# XML не допускает управляющие символы, а в тексте из PDF они встречаются.
_CONTROL = re.compile(r"[\x00-\x08\x0b\x0c\x0e-\x1f]")


def extract_text(filename: str, data: bytes) -> str:
    text = _extract(filename, data)
    if len(text) > MAX_TEXT_CHARS:
        raise ValueError(f"В файле больше {MAX_TEXT_CHARS} символов текста")
    return text


def _extract(filename: str, data: bytes) -> str:
    name = filename.lower()
    if name.endswith(".docx"):
        with zipfile.ZipFile(io.BytesIO(data)) as z:
            if sum(i.file_size for i in z.infolist()) > MAX_UNPACKED_BYTES:
                raise ValueError("Файл .docx слишком большой после распаковки")
        doc = Document(io.BytesIO(data))
        return "\n".join(p.text for p in doc.paragraphs).strip()
    if name.endswith(".pdf"):
        reader = PdfReader(io.BytesIO(data))
        if len(reader.pages) > MAX_PDF_PAGES:
            raise ValueError(f"В PDF больше {MAX_PDF_PAGES} страниц")
        return "\n".join(p.extract_text() or "" for p in reader.pages).strip()
    if name.endswith((".txt", ".md", ".csv", ".tsv")):
        for enc in ("utf-8-sig", "cp1251"):
            try:
                return data.decode(enc).strip()
            except UnicodeDecodeError:
                continue
    raise ValueError("Поддерживаются файлы .docx, .pdf, .txt, .csv")


def to_docx(text: str) -> bytes:
    doc = Document()
    style = doc.styles["Normal"]
    style.font.name = "Times New Roman"
    style.font.size = Pt(14)
    for line in text.splitlines():
        doc.add_paragraph(_CONTROL.sub("", line))
    buf = io.BytesIO()
    doc.save(buf)
    return buf.getvalue()
