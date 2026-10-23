"""Демонстрационные сценарии: проверка по нормативке, письмо, таблицы."""

import json
import logging
from collections.abc import AsyncIterator
from pathlib import Path
from urllib.parse import quote

import httpx
from fastapi import APIRouter, HTTPException, UploadFile, status
from fastapi.responses import FileResponse, Response, StreamingResponse

from app import llm
from app.config import get_settings
from app.schemas import CheckIn, DocxIn, LetterIn, TableIn
from app.security import CurrentUser
from app.services import files, letters, normcheck, tables

router = APIRouter(prefix="/api/tools", tags=["tools"])
logger = logging.getLogger(__name__)
EXAMPLES = Path(__file__).resolve().parent.parent / "data" / "examples"


def _sse(event: str, data: object) -> str:
    return f"event: {event}\ndata: {json.dumps(data, ensure_ascii=False)}\n\n"


def _stream(events: AsyncIterator[str]) -> StreamingResponse:
    async def guarded() -> AsyncIterator[str]:
        try:
            async for e in events:
                yield e
        except (llm.LLMError, ValueError) as e:
            yield _sse("error", {"message": str(e)})
        except Exception:
            logger.exception("Ошибка обработки запроса")
            yield _sse("error", {"message": "Внутренняя ошибка сервиса"})
        yield _sse("done", {})

    return StreamingResponse(
        guarded(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )


async def _tokens(messages: list[llm.Message]) -> AsyncIterator[str]:
    async for delta in llm.stream_chat(messages):
        yield _sse("delta", delta)


@router.get("/examples")
def examples(_: CurrentUser) -> dict:
    return {
        kind: json.loads((EXAMPLES / f"{kind}.json").read_text(encoding="utf-8"))
        for kind in ("check", "letters", "tables")
    }


@router.get("/examples/docs/{kind}/{name}")
def example_doc(kind: str, name: str, _: CurrentUser) -> FileResponse:
    """Исходный документ образца — тот, из которого разобран текст."""
    docs = (EXAMPLES / "docs").resolve()
    path = (docs / kind / name).resolve()
    if path.parent.parent != docs or not path.is_file():
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Документ не найден")
    return FileResponse(path, filename=name)


@router.get("/status")
async def llm_status(_: CurrentUser) -> dict:
    model = get_settings().llm_model
    try:
        available = await llm.list_models()
    except (httpx.HTTPError, ValueError, KeyError) as e:
        return {"model": model, "online": False, "error": e.__class__.__name__}
    return {"model": model, "online": True, "loaded": model in available}


@router.post("/check")
def check(body: CheckIn, _: CurrentUser) -> StreamingResponse:
    findings = normcheck.find_references(body.text)

    async def events() -> AsyncIterator[str]:
        yield _sse("findings", findings)
        if normcheck.needs_rewrite(findings):
            async for e in _tokens(normcheck.build_messages(body.text, findings)):
                yield e

    return _stream(events())


@router.post("/letter")
def letter(body: LetterIn, _: CurrentUser) -> StreamingResponse:
    messages = letters.build_messages(body)
    return _stream(_tokens(messages))


@router.post("/table")
def table(body: TableIn, _: CurrentUser) -> StreamingResponse:
    try:
        header, rows = tables.parse_table(body.source)
    except ValueError as e:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, str(e)) from e
    columns = [c.strip() for c in body.columns if c.strip()]
    parts = tables.chunks(rows)

    async def events() -> AsyncIterator[str]:
        yield _sse("source", {"header": header, "rows": rows})
        for i, part in enumerate(parts):
            data = await llm.complete_json(tables.build_messages(part, columns, body.instructions))
            yield _sse("rows", tables.normalize_result(data, columns, len(part)))
            yield _sse("progress", {"done": i + 1, "total": len(parts)})

    return _stream(events())


@router.post("/extract")
async def extract(file: UploadFile, _: CurrentUser) -> dict:
    data = await file.read(files.MAX_BYTES + 1)
    if len(data) > files.MAX_BYTES:
        raise HTTPException(status.HTTP_413_REQUEST_ENTITY_TOO_LARGE, "Файл больше 10 МБ")
    try:
        return {"text": files.extract_text(file.filename or "", data)}
    except ValueError as e:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, str(e)) from e
    except Exception as e:
        # Парсеры docx/pdf бросают разнородные исключения на повреждённых файлах.
        logger.warning("Не удалось прочитать файл %s: %r", file.filename, e)
        raise HTTPException(
            status.HTTP_422_UNPROCESSABLE_ENTITY, "Не удалось прочитать файл"
        ) from e


@router.post("/docx")
def docx(body: DocxIn, _: CurrentUser) -> Response:
    name = quote(f"{body.filename}.docx")
    return Response(
        files.to_docx(body.text),
        media_type="application/vnd.openxmlformats-officedocument.wordprocessingml.document",
        headers={"Content-Disposition": f"attachment; filename*=UTF-8''{name}"},
    )
