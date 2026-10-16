from datetime import datetime

from pydantic import BaseModel, Field


class CertificateBase(BaseModel):
    common_name: str = Field(..., max_length=100)
    serial_number: str = Field(..., max_length=128)
    status: str = Field(default="valid", max_length=20)  # valid/revoked/expired
    not_before: datetime
    not_after: datetime
    revoked_at: datetime | None = None


class CertificateCreate(CertificateBase):
    pass


class CertificateUpdate(BaseModel):
    status: str | None = Field(default=None, max_length=20)
    revoked_at: datetime | None = None


class Certificate(CertificateBase):
    id: int
    vpn_server_id: int | None = None

    class Config:
        from_attributes = True
