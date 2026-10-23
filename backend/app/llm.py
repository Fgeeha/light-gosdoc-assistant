"""Клиент OpenAI-совместимого API (/v1/chat/completions)."""

import json
import re
from collections.abc import AsyncIterator

import httpx

from app.config import get_settings

_THINK_OPEN, _THINK_CLOSE = "<think>", "</think>"


class LLMError(RuntimeError):
    pass


def _request(messages: list[dict], model: str | None, **extra) -> tuple[str, dict, dict]:
    s = get_settings()
    body = {
        "model": model or s.llm_model,
        "messages": messages,
        "temperature": s.llm_temperature,
        **s.llm_extra_body,
        **extra,
    }
    headers = {"Authorization": f"Bearer {s.llm_api_key}"}
    return f"{s.llm_base_url.rstrip('/')}/chat/completions", body, headers


def _partial_tail(buf: str, tag: str) -> int:
    """Длина хвоста buf, который может оказаться началом tag."""
    for k in range(min(len(tag) - 1, len(buf)), 0, -1):
        if buf.endswith(tag[:k]):
            return k
    return 0


async def strip_think(chunks: AsyncIterator[str]) -> AsyncIterator[str]:
    """Вырезает рассуждения <think>…</think> из потока reasoning-моделей."""
    buf, inside = "", False
    async for chunk in chunks:
        buf += chunk
        while True:
            if inside:
                end = buf.find(_THINK_CLOSE)
                if end < 0:
                    buf = buf[-len(_THINK_CLOSE) :]
                    break
                buf, inside = buf[end + len(_THINK_CLOSE) :], False
            else:
                start = buf.find(_THINK_OPEN)
                if start < 0:
                    keep = _partial_tail(buf, _THINK_OPEN)
                    if len(buf) > keep:
                        yield buf[: len(buf) - keep]
                        buf = buf[len(buf) - keep :]
                    break
                if start:
                    yield buf[:start]
                buf, inside = buf[start + len(_THINK_OPEN) :], True
    if buf and not inside:
        yield buf


async def _raw_stream(messages: list[dict], model: str | None) -> AsyncIterator[str]:
    url, body, headers = _request(messages, model, stream=True)
    timeout = httpx.Timeout(get_settings().llm_timeout_seconds, connect=10)
    try:
        async with httpx.AsyncClient(timeout=timeout) as client:
            async with client.stream("POST", url, json=body, headers=headers) as r:
                if r.status_code >= 400:
                    detail = (await r.aread()).decode(errors="replace")[:500]
                    raise LLMError(f"LLM вернула {r.status_code}: {detail}")
                async for line in r.aiter_lines():
                    if not line.startswith("data:"):
                        continue
                    data = line[5:].strip()
                    if data == "[DONE]":
                        break
                    choices = json.loads(data).get("choices") or [{}]
                    # reasoning/reasoning_content намеренно игнорируются.
                    if delta := (choices[0].get("delta") or {}).get("content"):
                        yield delta
    except httpx.HTTPError as e:
        raise LLMError(f"Сервер модели недоступен: {e.__class__.__name__}") from e


def stream_chat(messages: list[dict], model: str | None = None) -> AsyncIterator[str]:
    return strip_think(_raw_stream(messages, model))


async def complete_json(messages: list[dict], model: str | None = None) -> dict:
    """Запрос с ответом в виде JSON-объекта."""
    parts = [c async for c in stream_chat(messages, model)]
    return parse_json_object("".join(parts))


def parse_json_object(text: str) -> dict:
    text = re.sub(r"^```(?:json)?|```$", "", text.strip(), flags=re.M).strip()
    start, end = text.find("{"), text.rfind("}")
    if start < 0 or end < start:
        raise LLMError("Модель не вернула JSON")
    try:
        return json.loads(text[start : end + 1])
    except json.JSONDecodeError as e:
        raise LLMError(f"Модель вернула некорректный JSON: {e.msg}") from e


async def list_models() -> list[str]:
    s = get_settings()
    async with httpx.AsyncClient(timeout=5) as client:
        r = await client.get(
            f"{s.llm_base_url.rstrip('/')}/models",
            headers={"Authorization": f"Bearer {s.llm_api_key}"},
        )
        r.raise_for_status()
        return [m["id"] for m in r.json().get("data", [])]
