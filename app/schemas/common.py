from pydantic import BaseModel


class Message(BaseModel):
    message: str


class Page(BaseModel):
    total: int
    page: int = 1
    size: int = 20
