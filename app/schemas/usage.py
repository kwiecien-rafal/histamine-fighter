"""Token-usage accounting attached to every LLM-backed API response.

The backend reports raw, provider-normalized token counts only; the approximate
cost is derived in the frontend, where the price table is a presentation concern
that changes far more often than the API contract (CLAUDE.md Section 19).
LangChain normalizes ``usage_metadata`` across all five providers, so these
counts mean the same thing whichever model answered.
"""

from pydantic import BaseModel, Field


class StepUsage(BaseModel):
    """Token usage of one model call within a response's agentic flow."""

    step: str = Field(description="The agent step that made the call, e.g. 'synthesize'.")
    input_tokens: int = Field(default=0, ge=0)
    output_tokens: int = Field(default=0, ge=0)
    total_tokens: int = Field(default=0, ge=0)
    reported: bool = Field(
        default=False,
        description="False when the provider returned no usage, so the zeros read "
        "as 'unknown' rather than 'free'.",
    )


class LLMUsage(BaseModel):
    """Token usage of every model call behind one API response.

    ``calls`` is the figure the transparency panel leads with — a single dish
    lookup is propose (1) + assess (1-2) + alternatives (1 per goal), so showing
    it makes the multi-step cost legible instead of hidden.
    """

    calls: int = Field(default=0, ge=0)
    input_tokens: int = Field(default=0, ge=0)
    output_tokens: int = Field(default=0, ge=0)
    total_tokens: int = Field(default=0, ge=0)
    steps: list[StepUsage] = Field(default_factory=list)
