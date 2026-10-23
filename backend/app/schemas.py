"""Схемы запросов и ответов API."""

from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field

Username = Field(min_length=3, max_length=64, pattern=r"^[A-Za-z0-9._-]+$")
# Лимит в байтах дополнительно проверяется в security.hash_password.
Password = Field(min_length=8, max_length=72)


class LoginIn(BaseModel):
    username: str = Field(max_length=64)
    password: str = Field(max_length=200)


class UserOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    username: str
    full_name: str
    is_admin: bool
    is_active: bool
    created_at: datetime


class UserCreate(BaseModel):
    username: str = Username
    full_name: str = Field(default="", max_length=200)
    password: str = Password
    is_admin: bool = False


class UserUpdate(BaseModel):
    full_name: str | None = Field(default=None, max_length=200)
    password: str | None = Field(default=None, min_length=8, max_length=72)
    is_admin: bool | None = None
    is_active: bool | None = None


class CheckIn(BaseModel):
    text: str = Field(min_length=1, max_length=60_000)


class LetterIn(BaseModel):
    incoming: str = Field(min_length=1, max_length=30_000)
    organization: str = Field(default="", max_length=300)
    signer: str = Field(default="", max_length=300)
    instructions: str = Field(default="", max_length=5_000)


class TableIn(BaseModel):
    source: str = Field(min_length=1, max_length=300_000)
    columns: list[str] = Field(min_length=1, max_length=40)
    instructions: str = Field(default="", max_length=5_000)


class DocxIn(BaseModel):
    text: str = Field(min_length=1, max_length=100_000)
    filename: str = Field(default="document", max_length=100)
