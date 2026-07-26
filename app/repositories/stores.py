from sqlalchemy.orm import Session
from app.models.store import Store
from app.repositories.base import BaseRepository


class StoreRepository(BaseRepository[Store]):
    def __init__(self):
        super().__init__(Store)

    def active(self, db: Session) -> list[Store]:
        return db.query(Store).filter(Store.is_active.is_(True)).order_by(Store.id.desc()).all()

stores = StoreRepository()
