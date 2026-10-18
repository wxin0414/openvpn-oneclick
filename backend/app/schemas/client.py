from datetime import datetime

from pydantic import BaseModel, Field, field_validator

from app.services.names import NAME_PATTERN, ensure_safe_name


class ClientBase(BaseModel):
    name: str = Field(..., max_length=64, pattern=NAME_PATTERN)
    common_name: str = Field(..., max_length=64, pattern=NAME_PATTERN)
    fixed_ip: str | None = Field(default=None, max_length=50)
    routes: str | None = Field(default=None, max_length=500)
    # 到期时间与流量配额：为空表示不过期 / 不限量
    expires_at: datetime | None = None
    quota_mb: int | None = Field(default=None, ge=0, le=10000000)
    group_name: str | None = Field(default=None, max_length=64)

    @field_validator("name", "common_name")
    @classmethod
    def _check_name(cls, value: str) -> str:
        # pattern 只能表达字符集（Rust 正则不支持前瞻），"不能含 .." 在这里拦
        ensure_safe_name(value, "客户端名称")
        return value


class ClientCreate(ClientBase):
    status: str | None = "offline"


class ClientUpdate(BaseModel):
    fixed_ip: str | None = Field(default=None, max_length=50)
    routes: str | None = Field(default=None, max_length=500)
    status: str | None = None
    # 真正的"禁用"：会写进 CCD 的 disable 行并断开在线会话
    disabled: bool | None = None
    # 到期时间 / 配额（MB）：改这两个值也会触发 CCD 重算
    expires_at: datetime | None = None
    quota_mb: int | None = Field(default=None, ge=0, le=10000000)
    group_name: str | None = Field(default=None, max_length=64)


class ClientOnline(BaseModel):
    common_name: str
    real_address: str
    virtual_address: str
    bytes_received: int
    bytes_sent: int
    connected_since: str
    client_id: str | None = None


class Client(ClientBase):
    id: int
    status: str
    disabled: bool = False
    created_at: datetime

    class Config:
        from_attributes = True


class ClientPage(BaseModel):
    items: list[Client]
    total: int
    page: int
    page_size: int
