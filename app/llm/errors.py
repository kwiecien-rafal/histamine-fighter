"""Domain errors for the LLM layer.

The provider-resolution code knows nothing about HTTP. It raises these instead
of ``fastapi.HTTPException`` so it works the same whether it is called from a
request or from a script with no request in scope (the daily-suggestions cron).
The API boundary translates them to status codes; other callers handle them as
ordinary exceptions.
"""


class LLMError(Exception):
    """Base class for LLM-layer errors."""


class LLMConfigError(LLMError):
    """The LLM configuration is invalid: a missing API key or an unknown provider.

    Translated to HTTP 400 at the API boundary.
    """


class ProviderNotAvailableError(LLMError):
    """A recognised provider that is reserved for a later phase (e.g. ``modal``).

    Translated to HTTP 501 at the API boundary.
    """


class LLMInvocationError(LLMError):
    """A model call failed while running an agent.

    Covers an upstream provider error and, most often here, a model that cannot
    do tool calls (e.g. a small local Ollama model). There is no reliable way to
    detect that at resolution, so the agent surfaces it as this one error rather
    than a raw exception deep in the loop. Translated to HTTP 502 at the boundary.
    """


class LLMRejectedError(LLMInvocationError):
    """The provider refused the call: an unknown or unentitled model, a bad key, a request it will not accept."""


# Statuses a provider returns when the call is unusable as sent, rather than when
# it is momentarily unable to serve it.
_REJECTION_STATUSES = frozenset({400, 401, 403, 404, 422})


def rejection_status(exc: BaseException) -> int | None:
    """The provider's status when it refused the call, else None."""
    response = getattr(exc, "response", None)
    candidates = (
        getattr(exc, "status_code", None),
        getattr(exc, "code", None),
        getattr(response, "status_code", None),
    )
    return next(
        (
            status
            for status in candidates
            if isinstance(status, int) and status in _REJECTION_STATUSES
        ),
        None,
    )
