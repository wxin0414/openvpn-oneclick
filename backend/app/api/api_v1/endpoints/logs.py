from datetime import datetime

from fastapi import APIRouter, Depends, Query
from sqlalchemy.orm import Session

from app import crud
from app.api import deps
from app.api.security import get_current_user
from app.schemas.audit_log import AuditLogPage

router = APIRouter(dependencies=[Depends(get_current_user)])


@router.get("/audit", response_model=AuditLogPage, summary="List audit logs with pagination")
def list_audit_logs(
    page: int = Query(1, ge=1),
    page_size: int = Query(20, ge=1, le=200),
    action: str | None = Query(default=None),
    start_time: datetime | None = Query(default=None),
    end_time: datetime | None = Query(default=None),
    db: Session = Depends(deps.get_db),
) -> AuditLogPage:
    """审计日志分页查询。

    性能说明：``audit_logs.created_at`` 现在有索引（见 models/audit_log.py），
    ``(action, created_at)`` 也有复合索引，因此带 action 过滤的查询不再全表扫描。
    排序加上 ``id`` 作为次要键，避免同一时间戳下分页顺序不稳定导致漏行/重复。
    """
    model = crud.audit_log.model
    query = db.query(model)
    if action:
        query = query.filter(model.action == action)
    if start_time:
        query = query.filter(model.created_at >= start_time)
    if end_time:
        query = query.filter(model.created_at <= end_time)

    total = query.count()
    offset = (page - 1) * page_size
    items = query.order_by(model.created_at.desc(), model.id.desc()).offset(offset).limit(page_size).all()
    return AuditLogPage(items=items, total=total, page=page, page_size=page_size)


@router.get("/audit/export", summary="导出审计日志 CSV")
def export_audit_logs(
    limit: int = Query(5000, ge=1, le=50000),
    action: str | None = Query(default=None),
    db: Session = Depends(deps.get_db),
):
    """把审计日志导成 CSV。

    两个细节：

    * **文件头加 UTF-8 BOM**：否则 Excel 直接打开中文会是乱码（用户实际就是这么用的）。
    * 列名取自表结构而不是硬编码，加字段时导出跟着变，不会悄悄少一列。
    """
    import csv
    import io

    from fastapi.responses import StreamingResponse

    model = crud.audit_log.model
    query = db.query(model)
    if action:
        query = query.filter(model.action == action)
    rows = query.order_by(model.created_at.desc(), model.id.desc()).limit(limit).all()
    columns = [column.name for column in model.__table__.columns]

    def generate():
        buffer = io.StringIO()
        writer = csv.writer(buffer)
        writer.writerow(columns)
        # BOM：Excel 需要它才会按 UTF-8 解码
        yield "\ufeff" + buffer.getvalue()
        buffer.seek(0)
        buffer.truncate(0)
        for row in rows:
            writer.writerow([getattr(row, column, "") for column in columns])
            yield buffer.getvalue()
            buffer.seek(0)
            buffer.truncate(0)

    filename = f"audit-{datetime.now().strftime('%Y%m%d-%H%M%S')}.csv"
    return StreamingResponse(
        generate(),
        media_type="text/csv; charset=utf-8",
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )
