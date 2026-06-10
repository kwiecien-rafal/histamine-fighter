"""Request/response schemas for the Learn-hub RAG endpoints."""

from typing import Final

from pydantic import BaseModel, Field

# Single source of truth for the question length cap: the request schema enforces
# it at the API boundary; KnowledgeService re-checks it for non-HTTP callers.
MAX_QUESTION_LENGTH: Final = 500


class LearnQuery(BaseModel):
    question: str = Field(min_length=1, max_length=MAX_QUESTION_LENGTH)


class Citation(BaseModel):
    """A source the answer draws on, pointing back to a knowledge document."""

    title: str = Field(description="Document title.")
    source: str = Field(description="Citation/attribution for the document.")
    slug: str = Field(description="Document slug.")


class LearnAnswer(BaseModel):
    """The model's structured output for a knowledge question.

    The model writes the prose, judges whether the retrieved context covers the
    question, and reports which numbered passages it drew on. It never writes
    citation text itself — the agent maps the reported passage numbers back to
    the retrieved chunks, so citations always point at real sources the answer
    actually used.
    """

    answer: str = Field(description="The answer, using only the provided context.")
    sufficient: bool = Field(
        description="True only if the provided context actually answers the question."
    )
    used_passages: list[int] = Field(
        default_factory=list,
        description=(
            "Numbers of the context passages the answer draws on, e.g. [1, 3]. "
            "Empty when sufficient is false."
        ),
    )


class LearnResponse(BaseModel):
    """A grounded answer with its citations and the model that wrote it.

    A decline carries no prose: ``answer`` is null and ``grounded`` is false.
    Decline wording is display copy, owned by the client — the API stays
    neutral and localizable.
    """

    question: str
    answer: str | None = Field(
        default=None,
        description="The grounded answer; null when the question was declined.",
    )
    grounded: bool = Field(
        description="True when the answer is backed by retrieved sources."
    )
    citations: list[Citation] = Field(default_factory=list)
    model: str = Field(description="Which model produced the answer.")


class ArticleSummary(BaseModel):
    slug: str
    title: str
    topic: str


class ArticleListResponse(BaseModel):
    articles: list[ArticleSummary] = Field(default_factory=list)
