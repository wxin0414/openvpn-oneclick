from sqlalchemy.orm import Session

from app.crud.base import CRUDBase
from app.models.certificate import Certificate
from app.schemas.certificate import CertificateCreate, CertificateUpdate


class CRUDCertificate(CRUDBase[Certificate, CertificateCreate, CertificateUpdate]):
    def get_by_cn(self, db: Session, *, common_name: str) -> Certificate | None:
        return db.query(Certificate).filter(Certificate.common_name == common_name).first()

    def get_by_serial(self, db: Session, *, serial_number: str) -> Certificate | None:
        return db.query(Certificate).filter(Certificate.serial_number == serial_number).first()


certificate = CRUDCertificate(Certificate)
