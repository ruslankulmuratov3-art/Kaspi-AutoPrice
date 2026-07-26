from pydantic import BaseModel


class StoreBase(BaseModel):
    name: str
    merchant_id: str
    city: str = 'Алматы'
    is_active: bool = True


class StoreCreate(StoreBase):
    api_token: str = ''


class StoreUpdate(BaseModel):
    name: str | None = None
    merchant_id: str | None = None
    city: str | None = None
    api_token: str | None = None
    is_active: bool | None = None


class StoreRead(StoreBase):
    id: int
    owner_id: int | None = None

    model_config = {'from_attributes': True}
