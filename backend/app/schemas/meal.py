from pydantic import BaseModel, Field

from app.enums import SafetyLevel


class DishLookupRequest(BaseModel):
    dish: str = Field(min_length=1, max_length=200)


class Replacement(BaseModel):
    """One ingredient to swap out, and what to use instead."""

    ingredient: str = Field(description="The high-histamine ingredient to replace.")
    swap: str = Field(description="The histamine-safe ingredient to use instead.")
    reason: str = Field(description="Why the swap helps.")


class DishExplanation(BaseModel):
    """The prose the model writes for a dish lookup.

    The model does not decide the verdict: that is computed in code from the
    curated index. The model only identifies the dish and writes the explanation
    and swaps that justify the verdict it is given. It sets ``dish`` to the dish
    it found in the message, not a copy of the raw input, so extra text like
    "what is 2+2?" gets ignored.
    """

    dish: str = Field(description="The dish found in the user's message.")
    explanation: str = Field(description="Short reason for the verdict.")
    replacements: list[Replacement] = Field(
        default_factory=list,
        description="Safer swaps for the flagged ingredients. Empty when the verdict is 'safe'.",
    )


class DishLookupResponse(DishExplanation):
    """The dish verdict (owned by the index), its prose, and the model that wrote it."""

    verdict: SafetyLevel = Field(description="Overall histamine safety of the dish.")
    model: str = Field(description="Which model produced the verdict.")
