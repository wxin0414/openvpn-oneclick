from sqlalchemy.orm import Session

from app.crud.base import CRUDBase
from app.models.client import Client
from app.schemas.client import ClientCreate, ClientUpdate


class CRUDClient(CRUDBase[Client, ClientCreate, ClientUpdate]):
    def get_by_common_name(self, db: Session, *, common_name: str) -> Client | None:
        return db.query(Client).filter(Client.common_name == common_name).first()


client = CRUDClient(Client)
