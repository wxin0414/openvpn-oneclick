from sqlalchemy.orm import Session

from app.crud.base import CRUDBase
from app.models.vpn_server import VPNServer
from app.schemas.server import ServerCreate, ServerUpdate


class CRUDServer(CRUDBase[VPNServer, ServerCreate, ServerUpdate]):
    def get_by_name(self, db: Session, *, name: str) -> VPNServer | None:
        return db.query(VPNServer).filter(VPNServer.name == name).first()


server = CRUDServer(VPNServer)
