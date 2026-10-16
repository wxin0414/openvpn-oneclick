from pydantic import BaseModel


class Token(BaseModel):
    access_token: str
    token_type: str = "bearer"
    # 仍在使用公开默认口令时为 True，前端可提示立即修改
    must_change_password: bool = False


class PasswordChange(BaseModel):
    current_password: str
    new_password: str
