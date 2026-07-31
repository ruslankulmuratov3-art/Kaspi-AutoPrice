from pydantic import BaseModel, Field
from app.models.product import ProductStatus


class ProductBase(BaseModel):
    store_id: int
    kaspi_sku: str
    name: str
    category: str = ''
    brand: str = ''
    url: str = ''
    current_price: float = 0
    min_price: float = 0
    max_price: float = 0
    cost_price: float = 0
    stock: int = 0
    status: ProductStatus = ProductStatus.ACTIVE
    auto_pricing_enabled: bool = True


class ProductCreate(ProductBase):
    pass


class ProductUpdate(BaseModel):
    name: str | None = None
    category: str | None = None
    brand: str | None = None
    url: str | None = None
    current_price: float | None = Field(default=None, ge=0)
    min_price: float | None = Field(default=None, ge=0)
    max_price: float | None = Field(default=None, ge=0)
    cost_price: float | None = Field(default=None, ge=0)
    stock: int | None = None
    status: ProductStatus | None = None
    auto_pricing_enabled: bool | None = None


class ProductRead(ProductBase):
    id: int

    model_config = {'from_attributes': True}
