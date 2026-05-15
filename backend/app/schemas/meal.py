from typing import Literal

from pydantic import BaseModel, Field

Verdict = Literal["safe", "depends", "avoid"]


class DishLookupRequest(BaseModel):
    dish: str = Field(min_length=1, max_length=200)


class DishLookupResponse(BaseModel):
    dish: str
    verdict: Verdict
    explanation: str
    model: str
