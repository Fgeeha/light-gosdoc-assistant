"""Журнал событий безопасности: входы, учётные записи, правки справочника.

Отдельный логгер, чтобы его можно было направить в SIEM или отдельный файл, не трогая остальное.
"""

import logging

from fastapi import Request

audit = logging.getLogger("gosdoc.audit")


def client_ip(request: Request) -> str:
    # За nginx адрес приходит из X-Forwarded-For (uvicorn --proxy-headers).
    return request.client.host if request.client else "-"
