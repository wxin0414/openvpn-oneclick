"""用户管理（仅超级管理员）。

修复的问题
----------
原实现允许：
* 超级管理员**删除自己**的账号 → 当场把自己关在门外；
* 删除 / 停用 / 降级**最后一个可用的超级管理员** → 系统再也没有人能管理，
  只能去服务器上直接改数据库才能恢复。

这两种都是管理面板里代价很高的自锁场景，现在显式拦住，并给出明确提示。
"""

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.orm import Session

from app import crud
from app.api import deps
from app.api.security import get_current_user
from app.schemas.user import User, UserCreate, UserUpdate
from app.services.audit import safe_audit

router = APIRouter()


def ensure_superuser(user=Depends(get_current_user)):
    if not user.is_superuser:
        raise HTTPException(status_code=403, detail="Not enough privileges")
    return user


def _active_superuser_count(db: Session) -> int:
    model = crud.user.model
    return (
        db.query(model)
        .filter(model.is_superuser.is_(True), model.is_active.is_(True))
        .count()
    )


LAST_SUPERUSER_MESSAGE = "系统必须保留至少一个启用状态的超级管理员"


@router.get("/", response_model=list[User], dependencies=[Depends(ensure_superuser)])
def list_users(db: Session = Depends(deps.get_db)):
    # 查询用户列表（仅超级管理员）
    return crud.user.get_multi(db)


@router.post("/", response_model=User, status_code=status.HTTP_201_CREATED, dependencies=[Depends(ensure_superuser)])
def create_user(
    user_in: UserCreate,
    db: Session = Depends(deps.get_db),
    current=Depends(get_current_user),
):
    # 创建用户（仅超级管理员）
    existing = crud.user.get_by_username(db, username=user_in.username)
    if existing:
        raise HTTPException(status_code=400, detail="Username already exists")
    created = crud.user.create(db, user_in)
    safe_audit(db, current.username, "create_user", user_in.username, "success")
    return created


@router.patch("/{user_id}", response_model=User)
def update_user(
    user_id: int,
    user_in: UserUpdate,
    db: Session = Depends(deps.get_db),
    current=Depends(ensure_superuser),
):
    # 更新用户（仅超级管理员）
    db_obj = crud.user.get(db, user_id)
    if not db_obj:
        raise HTTPException(status_code=404, detail="User not found")

    # 只有在"本来是可用的超级管理员、现在要被拿掉权限或停用"时才需要保护
    losing_privilege = db_obj.is_superuser and db_obj.is_active and (
        user_in.is_superuser is False or user_in.is_active is False
    )
    if losing_privilege and _active_superuser_count(db) <= 1:
        raise HTTPException(status_code=400, detail=LAST_SUPERUSER_MESSAGE)

    if db_obj.id == current.id and user_in.is_active is False:
        raise HTTPException(status_code=400, detail="不能停用当前登录的账号")

    updated = crud.user.update(db, db_obj=db_obj, obj_in=user_in)
    safe_audit(db, current.username, "update_user", db_obj.username, "success")
    return updated


@router.delete("/{user_id}", response_model=User)
def delete_user(
    user_id: int,
    db: Session = Depends(deps.get_db),
    current=Depends(ensure_superuser),
):
    # 删除用户（仅超级管理员）
    db_obj = crud.user.get(db, user_id)
    if not db_obj:
        raise HTTPException(status_code=404, detail="User not found")

    if db_obj.id == current.id:
        raise HTTPException(status_code=400, detail="不能删除当前登录的账号")
    if db_obj.is_superuser and db_obj.is_active and _active_superuser_count(db) <= 1:
        raise HTTPException(status_code=400, detail=LAST_SUPERUSER_MESSAGE)

    username = db_obj.username
    removed = crud.user.remove(db, user_id)
    if not removed:
        raise HTTPException(status_code=404, detail="User not found")
    safe_audit(db, current.username, "delete_user", username, "success")
    return removed
