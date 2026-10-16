from datetime import datetime

from pydantic import BaseModel, Field


class AuditLogBase(BaseModel):
    actor: str = Field(..., max_length=100)
    action: str = Field(..., max_length=200)
    target: str | None = Field(default=None, max_length=200)
    result: str = Field(default="success", max_length=50)


class AuditLogCreate(AuditLogBase):
    pass


class AuditLog(AuditLogBase):
    id: int
    created_at: datetime

    class Config:
        from_attributes = True


class AuditLogPage(BaseModel):
    items: list[AuditLog]
    total: int
    page: int
    page_size: int
