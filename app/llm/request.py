"""The per-request LLM config and its deferred shared-tier charge.

Lives in the LLM package rather than beside the FastAPI providers so the service
layer can hold it: ``app.dependencies`` imports every service, so a service that
reached back for this type would close an import cycle. Nothing here touches
HTTP — resolution stays in ``app.dependencies``, which builds this and hands it
down.
"""

from collections.abc import Awaitable, Callable
from dataclasses import dataclass

from app.llm.config import LLMRequestConfig


@dataclass
class RequestLLM:
    """A resolved per-request LLM config and its deferred shared-tier charge.

    The charge is held back until the caller reaches the actual model call, past
    body validation, the burst limiter, and the Learn cache, so a rejected or
    cached request never spends the daily allowance. ``charge`` is a one-shot:
    the first call spends, later calls are no-ops, and a failed charge does not
    re-arm (the request is already being rejected). A caller that resolves the
    shared config but then makes no model call (a cache hit) calls ``waive``, so
    the charge is released without spending it and the leak backstop stays quiet.
    """

    config: LLMRequestConfig
    # True only when the config was pinned to the operator-funded shared tier.
    # Explicit rather than inferred from ``pending``: ``charge()`` consumes the
    # callable before the lookup-cache write gate needs the answer.
    shared: bool = False
    _charge: Callable[[], Awaitable[None]] | None = None

    @property
    def pending(self) -> bool:
        """Whether a shared-tier charge is still unspent (the backstop's check)."""
        return self._charge is not None

    async def charge(self) -> None:
        """Spend the deferred shared-tier charge, once."""
        charge = self._charge
        if charge is None:
            return
        self._charge = None
        await charge()

    def waive(self) -> None:
        """Release the pending shared-tier charge without spending it.

        For a caller that resolved the shared config but served the request with no
        model call (a cache hit): the answer costs nothing, so the daily allowance
        is untouched and the charge-leak backstop must not read the deliberate skip
        as a forgotten charge. Unlike a forgotten charge this is an expected
        outcome, so it stays silent.
        """
        self._charge = None
