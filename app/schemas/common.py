from pydantic import BaseModel, Field


class PaginatedResponse(BaseModel):
    items: list
    total: int


class MessageOut(BaseModel):
    detail: str = "ok"
