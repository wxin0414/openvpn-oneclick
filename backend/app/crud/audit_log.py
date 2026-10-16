from sqlalchemy.orm import Session

from app.crud.base import CRUDBase
from app.models.audit_log import AuditLog
from app.schemas.audit_log import AuditLogCreate


class CRUDAuditLog(CRUDBase[AuditLog, AuditLogCreate, AuditLogCreate]):
    def get_recent(self, db: Session, *, limit: int = 10) -> list[AuditLog]:
        return (
            db.query(self.model)
            .order_by(self.model.created_at.desc())
            .limit(limit)
            .all()
        )


audit_log = CRUDAuditLog(AuditLog)
