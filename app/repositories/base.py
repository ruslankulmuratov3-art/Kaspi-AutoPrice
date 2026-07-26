from typing import Generic, TypeVar, Type, Iterable
from sqlalchemy.orm import Session
from app.core.database import Base

ModelType = TypeVar('ModelType', bound=Base)


class BaseRepository(Generic[ModelType]):
    def __init__(self, model: Type[ModelType]):
        self.model = model

    def get(self, db: Session, item_id: int) -> ModelType | None:
        return db.query(self.model).filter(self.model.id == item_id).first()

    def list(self, db: Session, skip: int = 0, limit: int = 100) -> list[ModelType]:
        return db.query(self.model).offset(skip).limit(limit).all()

    def create(self, db: Session, **kwargs) -> ModelType:
        obj = self.model(**kwargs)
        db.add(obj)
        db.commit()
        db.refresh(obj)
        return obj

    def update(self, db: Session, obj: ModelType, **kwargs) -> ModelType:
        for key, value in kwargs.items():
            if value is not None and hasattr(obj, key):
                setattr(obj, key, value)
        db.add(obj)
        db.commit()
        db.refresh(obj)
        return obj

    def delete(self, db: Session, obj: ModelType) -> None:
        db.delete(obj)
        db.commit()

    def bulk_create(self, db: Session, rows: Iterable[dict]) -> int:
        objects = [self.model(**row) for row in rows]
        db.add_all(objects)
        db.commit()
        return len(objects)
