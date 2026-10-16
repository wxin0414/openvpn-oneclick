from datetime import datetime

from pydantic import BaseModel, Field


class ServerBase(BaseModel):
    name: str = Field(..., max_length=100)
    host: str = Field(..., max_length=255)
    port: int = Field(default=1194, ge=1, le=65535)
    protocol: str = Field(default="tcp", pattern="^(tcp|udp)$")
    status: str | None = Field(default=None)
    vpn_ip: str | None = Field(default=None, max_length=50)


class ServerCreate(ServerBase):
    status: str | None = Field(default="stopped")


class ServerUpdate(BaseModel):
    host: str | None = Field(default=None, max_length=255)
    port: int | None = Field(default=None, ge=1, le=65535)
    protocol: str | None = Field(default=None, pattern="^(tcp|udp)$")
    status: str | None = Field(default=None)
    vpn_ip: str | None = Field(default=None, max_length=50)


class Server(ServerBase):
    id: int
    created_at: datetime

    class Config:
        from_attributes = True
