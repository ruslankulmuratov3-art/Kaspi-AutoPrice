from sqlalchemy.orm import Session
from app.models.product import Product, ProductStatus
from app.repositories.base import BaseRepository


class ProductRepository(BaseRepository[Product]):
    def __init__(self):
        super().__init__(Product)

    def active(self, db: Session) -> list[Product]:
        return db.query(Product).filter(Product.status == ProductStatus.ACTIVE).order_by(Product.id.desc()).all()

    def for_store(self, db: Session, store_id: int) -> list[Product]:
        return db.query(Product).filter(Product.store_id == store_id).order_by(Product.id.desc()).all()

    def search(self, db: Session, query: str, limit: int = 50) -> list[Product]:
        q = f'%{query}%'
        return db.query(Product).filter((Product.name.ilike(q)) | (Product.kaspi_sku.ilike(q))).limit(limit).all()

products = ProductRepository()
