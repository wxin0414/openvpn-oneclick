from datetime import datetime

from pydantic import BaseModel, Field


class ClientBase(BaseModel):
    name: str = Field(..., max_length=100)
    common_name: str = Field(..., max_length=100)
    fixed_ip: str | None = Field(default=None, max_length=50)
    routes: str | None = Field(default=None, max_length=500)


class ClientCreate(ClientBase):
    status: str | None = "offline"


class ClientUpdate(BaseModel):
    fixed_ip: str | None = Field(default=None, max_length=50)
    routes: str | None = Field(default=None, max_length=500)
    status: str | None = None


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
    created_at: datetime

    class Config:
        from_attributes = True


class ClientPage(BaseModel):
    items: list[Client]
    total: int
    page: int
    page_size: int
