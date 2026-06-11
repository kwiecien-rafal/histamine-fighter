"""The Learn-hub RAG agent: retrieve curated knowledge, answer with citations.

Classic retrieval-augmented generation. ``run`` embeds the question, retrieves
the knowledge chunks above the similarity floor, and asks the model to answer
using only those passages. Grounding guarantees: when nothing relevant is
retrieved the model is never called (no context, no answer); the model reports
which numbered passages it drew on, and citations are those passages' source
documents — validated against the context that was actually sent, never taken
from free text. An answer that claims sufficiency but attributes no passage is
declined rather than served uncited.
"""

from collections.abc import AsyncIterator

import structlog
from langchain_core.messages import BaseMessage, HumanMessage, SystemMessage

from app.agents.base import BaseAgent, loggable_messages
from app.agents.prompting import load_prompt, render_prompt, strip_closing_tag
from app.llm.errors import LLMInvocationError
from app.llm.langchain_factory import ChatModel
from app.schemas.learn import Citation, LearnAnswer, LearnResponse
from app.services.knowledge_service import KnowledgeMatch, KnowledgeService

log = structlog.get_logger(__name__)

_INVOCATION_ERROR = (
    "The language model failed to answer the knowledge question. If you selected "
    "a custom model, make sure it supports structured output."
)


class LearnAgent(BaseAgent):
    """Answers histamine questions grounded in the curated knowledge corpus."""

    def __init__(self, chat: ChatModel, service: KnowledgeService, *, k: int = 5) -> None:
        super().__init__(chat)
        self._service = service
        self._system_prompt = render_prompt(
            load_prompt("learn/system"),
            "learn/system",
            input_tag="<context> and <question>",
        )
        self._user_template = load_prompt("learn/user")
        self._k = k

    def stream(self, question: str) -> AsyncIterator[str]:
        # Declared, not omitted, so the streaming contract stays explicit; deferred.
        raise NotImplementedError("Streaming learn answers is not implemented yet.")

    async def run(self, question: str) -> LearnResponse:
        log.debug("learn.start", question=question[:80], model=self._chat.model_name)
        chunks = await self._service.search(question, k=self._k)
        if not chunks:
            log.info("learn.no_context", question=question[:80])
            return self._declined(question)

        answer = await self._answer(question, chunks)
        if not answer.sufficient:
            log.info(
                "learn.insufficient_context",
                question=question[:80],
                retrieved=len(chunks),
            )
            return self._declined(question)

        citations = self._citations(chunks, answer.used_passages)
        if not citations:
            # Sufficient but unattributable: the model answered without pointing at
            # any real passage. Serving that would be an uncited health claim.
            log.warning(
                "learn.uncited_answer",
                question=question[:80],
                used_passages=answer.used_passages,
                retrieved=len(chunks),
                model=self._chat.model_name,
            )
            return self._declined(question)
        log.info(
            "learn.answered",
            question=question[:80],
            retrieved=len(chunks),
            sources=[citation.slug for citation in citations],
            model=self._chat.model_name,
        )
        return LearnResponse(
            question=question,
            answer=answer.answer,
            grounded=True,
            citations=citations,
            model=self._chat.model_name,
        )

    def _declined(self, question: str) -> LearnResponse:
        # No prose: the decline wording is the client's display copy, not API data.
        return LearnResponse(
            question=question,
            answer=None,
            grounded=False,
            citations=[],
            model=self._chat.model_name,
        )

    async def _answer(self, question: str, chunks: list[KnowledgeMatch]) -> LearnAnswer:
        prompt = render_prompt(
            self._user_template,
            "learn/user",
            passages=self._format_context(chunks),
            question=strip_closing_tag(question, "question"),
        )
        messages: list[BaseMessage] = [
            SystemMessage(self._system_prompt),
            HumanMessage(prompt),
        ]
        log.debug("learn.request", messages=loggable_messages(messages))
        structured = self._chat.model.with_structured_output(LearnAnswer)
        try:
            result = await structured.ainvoke(messages)
        except Exception as exc:
            raise LLMInvocationError(_INVOCATION_ERROR) from exc
        # with_structured_output can yield None (refusal/parse miss) or a dict on
        # some providers; only a validated LearnAnswer may cross this boundary.
        if not isinstance(result, LearnAnswer):
            log.warning(
                "learn.malformed_structured_output",
                result_type=type(result).__name__,
                model=self._chat.model_name,
            )
            raise LLMInvocationError(_INVOCATION_ERROR)
        log.debug("learn.reply", answer=result.model_dump())
        return result

    @staticmethod
    def _format_context(chunks: list[KnowledgeMatch]) -> str:
        return "\n\n".join(
            f"[{index}] {match.chunk.title}\n{match.chunk.content}"
            for index, match in enumerate(chunks, start=1)
        )

    @staticmethod
    def _citations(chunks: list[KnowledgeMatch], used_passages: list[int]) -> list[Citation]:
        """One citation per source document the answer drew on, in retrieval order.

        Passage numbers come from the model, so they are validated against the
        context that was sent (1-based, as displayed); anything out of range is
        ignored rather than trusted.
        """
        used = {number for number in used_passages if 1 <= number <= len(chunks)}
        seen: set[str] = set()
        citations: list[Citation] = []
        for number, match in enumerate(chunks, start=1):
            if number not in used or match.chunk.slug in seen:
                continue
            seen.add(match.chunk.slug)
            citations.append(
                Citation(
                    title=match.chunk.title,
                    source=match.chunk.source,
                    slug=match.chunk.slug,
                )
            )
        return citations
