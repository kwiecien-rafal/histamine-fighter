from abc import ABC, abstractmethod
from collections.abc import AsyncIterator, Sequence
from typing import Any, cast

import structlog
from langchain_core.messages import AIMessage, BaseMessage
from pydantic import BaseModel

from app.llm.errors import LLMInvocationError
from app.llm.langchain_factory import ChatModel
from app.schemas.usage import LLMUsage, StepUsage

log = structlog.get_logger(__name__)


def loggable_messages(messages: Sequence[BaseMessage]) -> list[dict[str, str]]:
    """Messages as role/content pairs for the per-call ``*.request`` debug events.

    Prompts are logged at the invocation boundary — when they are sent, not when
    their templates render — so one request's debug log reads chronologically:
    each model call's ``request`` event, then its reply.
    """
    return [{"role": message.type, "content": str(message.content)} for message in messages]


def _step_usage(step: str, message: BaseMessage) -> StepUsage:
    """Read one reply's token usage, normalized by LangChain across providers.

    A model that does not report usage yields ``None`` here; the step is still
    recorded (the call was made) but flagged unreported, so the panel can show
    that rather than imply the call was free.
    """
    usage = message.usage_metadata if isinstance(message, AIMessage) else None
    if usage is None:
        return StepUsage(step=step)
    return StepUsage(
        step=step,
        input_tokens=usage["input_tokens"],
        output_tokens=usage["output_tokens"],
        total_tokens=usage["total_tokens"],
        reported=True,
    )


class BaseAgent(ABC):
    """Shared base for the LLM agents (CLAUDE Section 8).

    Holds the resolved :class:`ChatModel` — the single LLM seam, so an agent never
    builds a client of its own — and exposes the model name for the transparency
    badge. :meth:`_structured_invoke` is the one place a structured-output call is
    made, so it is also where token usage is tallied. Each agent implements its own
    typed ``run``; ``stream`` is declared here so the SSE contract cannot be
    silently dropped by a subclass (an agent that has not implemented streaming yet
    must say so explicitly).
    """

    # User-facing message when a structured-output call fails or returns no parse;
    # subclasses override it with wording specific to their flow.
    _invocation_error = "The language model failed to complete the request."

    def __init__(self, chat: ChatModel) -> None:
        self._chat = chat
        # Per-call usage for the response currently being built. Safe as instance
        # state because agents are request-scoped — one per request, wired in
        # app/dependencies.py.
        self._calls: list[StepUsage] = []

    @property
    def model_name(self) -> str:
        return self._chat.model_name

    @abstractmethod
    def stream(self, *args: Any, **kwargs: Any) -> AsyncIterator[str]:
        """Stream the agent's answer as SSE text chunks.

        Declared loose here so each agent can type its own signature (mirroring
        its ``run``) without violating the override contract.
        """
        ...

    async def _structured_invoke[SchemaT: BaseModel](
        self, schema: type[SchemaT], messages: list[BaseMessage], *, step: str
    ) -> SchemaT:
        """Make one structured-output call, tallying its token usage.

        ``include_raw`` keeps the reply message beside the parsed object so its
        ``usage_metadata`` can be read — ``with_structured_output`` on its own
        returns only the parse and discards the usage. Every failure maps to the
        agent's domain error, including the silent one: a function-calling model
        that answers in prose yields ``parsed=None`` instead of raising. The call
        is tallied before that check, so a model that spent tokens and then failed
        to emit the tool call is still counted.
        """
        structured = self._chat.model.with_structured_output(schema, include_raw=True)
        try:
            raw = cast(dict[str, Any], await structured.ainvoke(messages))
            reply, parsed = raw["raw"], raw["parsed"]
        except Exception as exc:
            raise LLMInvocationError(self._invocation_error) from exc
        self._tally(reply, step=step)
        if parsed is None:
            log.warning("agent.malformed_structured_output", step=step, model=self.model_name)
            raise LLMInvocationError(self._invocation_error)
        return cast(SchemaT, parsed)

    def _tally(self, reply: BaseMessage, *, step: str) -> None:
        """Record one model reply's token usage on the in-progress response tally.

        Both ``_structured_invoke`` and the composer's manual tool loop route
        through here, so a single structured call and a multi-iteration loop report
        usage the same way and the transparency panel stays accurate.
        """
        self._calls.append(_step_usage(step, reply))

    def _begin_usage(self) -> None:
        """Start a fresh usage tally for the response about to be built."""
        self._calls = []

    def _collect_usage(self) -> LLMUsage:
        """Total the calls tallied since the last :meth:`_begin_usage`.

        Only a returned response carries usage: a public method that raises
        partway (e.g. assess failing at synthesis after a disambiguate call) never
        reaches here, so those tokens are not reported — consistent with the
        frontend recording usage only on a successful response.
        """
        return LLMUsage(
            calls=len(self._calls),
            input_tokens=sum(call.input_tokens for call in self._calls),
            output_tokens=sum(call.output_tokens for call in self._calls),
            total_tokens=sum(call.total_tokens for call in self._calls),
            steps=list(self._calls),
        )
