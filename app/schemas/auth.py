"""Request and response schemas for the public auth flows (magic link, OAuth)."""

from datetime import datetime

from pydantic import BaseModel, ConfigDict, EmailStr, Field, model_validator

from app.enums import Role

# Generous bound: just enough to reject absurd payloads.
MAX_EMAIL_CHARS = 320

MAGIC_CODE_LENGTH = 6


class AuthUser(BaseModel):
    """The signed-in user as the SPA sees it: enough to gate the UI, no token."""

    model_config = ConfigDict(from_attributes=True)

    email: str
    role: Role


class MagicLinkRequest(BaseModel):
    """Ask for a sign-in email. The Turnstile token is required only when the
    deployment has Turnstile configured; the backend decides."""

    # EmailStr runs email-validator: the address is the entire credential here,
    # so a syntactically broken one must die at the schema, not at Resend.
    email: EmailStr = Field(max_length=MAX_EMAIL_CHARS)
    turnstile_token: str | None = Field(default=None, max_length=4096)


class MagicLinkVerify(BaseModel):
    """Complete a sign-in: either the link's token, or the email + 6-digit code."""

    token: str | None = Field(default=None, max_length=4096)
    email: EmailStr | None = Field(default=None, max_length=MAX_EMAIL_CHARS)
    code: str | None = Field(
        default=None, min_length=MAGIC_CODE_LENGTH, max_length=MAGIC_CODE_LENGTH
    )

    @model_validator(mode="after")
    def _exactly_one_path(self) -> "MagicLinkVerify":
        by_token = self.token is not None
        by_code = self.email is not None and self.code is not None
        if by_token == by_code:
            raise ValueError("Provide either the link token, or an email and code.")
        return self


class QuotaRead(BaseModel):
    """The signed-in user's shared-tier allowance today."""

    used: int
    limit: int
    resets_at: datetime
