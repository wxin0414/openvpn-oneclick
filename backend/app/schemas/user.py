from datetime import datetime

from typing import Literal

from pydantic import BaseModel, EmailStr, Field


class UserBase(BaseModel):
    username: str = Field(..., max_length=50)
    email: EmailStr | None = None
    is_active: bool = True
    is_superuser: bool = False
    role: Literal["admin", "viewer"] = "admin"


class UserCreate(UserBase):
    password: str = Field(..., min_length=6)


class UserUpdate(BaseModel):
    email: EmailStr | None = None
    password: str | None = Field(default=None, min_length=6)
    is_active: bool | None = None
    is_superuser: bool | None = None
    role: Literal["admin", "viewer"] | None = None


class User(UserBase):
    id: int
    created_at: datetime

    class Config:
        from_attributes = True
