"""Tests for the LearnAgent RAG loop.

A scripted chat model returns a fixed structured answer and a stub service returns
fixed chunks, so these exercise the grounding rules — citations mapped from the
passages the model reports using (validated against what was sent), declining on
insufficient, absent, or unattributable context — with no network call and no
embedding model.
"""

import pytest

from app.agents.learn import LearnAgent
from app.llm.errors import LLMInvocationError
from app.llm.langchain_factory import ChatModel
from app.models import KnowledgeChunk
from app.schemas.learn import LearnAnswer
from app.services.knowledge_service import KnowledgeMatch


def _match(
    slug: str, *, title: str = "Title", source: str = "Source"
) -> KnowledgeMatch:
    chunk = KnowledgeChunk(
        slug=slug,
        title=title,
        source=source,
        topic="basics",
        chunk_index=0,
        content="some passage text",
        embedding=[],
    )
    return KnowledgeMatch(chunk=chunk, similarity=0.9)


class _StubService:
    def __init__(self, chunks: list[KnowledgeMatch]) -> None:
        self._chunks = chunks

    async def search(self, query: str, k: int | None = None) -> list[KnowledgeMatch]:
        return self._chunks


# What structured.ainvoke can actually hand back: a parsed answer, but also None
# or a raw dict on some provider/json-mode combinations, or an exception.
_ScriptedResult = LearnAnswer | dict[str, object] | Exception | None


class _Structured:
    def __init__(self, result: _ScriptedResult) -> None:
        self._result = result

    async def ainvoke(self, _messages: object) -> _ScriptedResult:
        if isinstance(self._result, Exception):
            raise self._result
        return self._result


class _ScriptedChat:
    def __init__(self, result: _ScriptedResult) -> None:
        self._result = result
        self.calls = 0

    def with_structured_output(self, _schema: object) -> _Structured:
        self.calls += 1
        return _Structured(self._result)


def _agent(chat: _ScriptedChat, service: _StubService) -> LearnAgent:
    wrapper = ChatModel(model=chat, model_name="stub/model")  # type: ignore[arg-type]
    return LearnAgent(chat=wrapper, service=service)  # type: ignore[arg-type]


async def test_answers_and_cites_used_sources() -> None:
    service = _StubService([_match("dao", title="DAO"), _match("foods", title="Foods")])
    chat = _ScriptedChat(
        LearnAnswer(
            answer="Diamine oxidase clears histamine.",
            sufficient=True,
            used_passages=[1, 2],
        )
    )

    result = await _agent(chat, service).run("how is histamine broken down")

    assert result.grounded is True
    assert result.answer == "Diamine oxidase clears histamine."
    assert [citation.slug for citation in result.citations] == ["dao", "foods"]
    assert result.model == "stub/model"


async def test_cites_only_passages_the_answer_used() -> None:
    service = _StubService([_match("dao", title="DAO"), _match("foods", title="Foods")])
    chat = _ScriptedChat(
        LearnAnswer(
            answer="Aged cheese is high in histamine.",
            sufficient=True,
            used_passages=[2],
        )
    )

    result = await _agent(chat, service).run("which foods are high in histamine")

    assert result.grounded is True
    assert [citation.slug for citation in result.citations] == ["foods"]


async def test_out_of_range_passage_numbers_are_ignored() -> None:
    service = _StubService([_match("dao"), _match("foods")])
    chat = _ScriptedChat(
        LearnAnswer(answer="ok", sufficient=True, used_passages=[0, 2, 7, -1])
    )

    result = await _agent(chat, service).run("question")

    assert result.grounded is True
    assert [citation.slug for citation in result.citations] == ["foods"]


async def test_sufficient_answer_without_valid_citations_declines() -> None:
    """An answer that attributes no real passage is uncited — decline, don't serve."""
    service = _StubService([_match("dao")])
    chat = _ScriptedChat(
        LearnAnswer(answer="confident prose", sufficient=True, used_passages=[9])
    )

    result = await _agent(chat, service).run("question")

    assert result.grounded is False
    assert result.citations == []
    assert result.answer is None


async def test_insufficient_context_declines() -> None:
    service = _StubService([_match("dao")])
    chat = _ScriptedChat(LearnAnswer(answer="", sufficient=False))

    result = await _agent(chat, service).run("an unrelated question")

    assert result.grounded is False
    assert result.citations == []
    assert result.answer is None  # decline copy belongs to the client, not the API


async def test_no_context_declines_without_calling_model() -> None:
    service = _StubService([])
    chat = _ScriptedChat(LearnAnswer(answer="should never be used", sufficient=True))

    result = await _agent(chat, service).run("anything")

    assert result.grounded is False
    assert result.answer is None
    assert result.citations == []
    assert chat.calls == 0  # the model is never invoked when nothing was retrieved


async def test_duplicate_document_is_cited_once() -> None:
    service = _StubService(
        [_match("dao", title="DAO part 1"), _match("dao", title="DAO part 2")]
    )
    chat = _ScriptedChat(
        LearnAnswer(answer="ok", sufficient=True, used_passages=[1, 2])
    )

    result = await _agent(chat, service).run("question")

    assert [citation.slug for citation in result.citations] == ["dao"]


async def test_model_failure_becomes_clean_domain_error() -> None:
    service = _StubService([_match("dao")])
    chat = _ScriptedChat(RuntimeError("model down"))

    with pytest.raises(LLMInvocationError):
        await _agent(chat, service).run("question")


async def test_none_structured_output_becomes_clean_domain_error() -> None:
    """A refusal/parse miss surfaces as None — never as an AttributeError later."""
    service = _StubService([_match("dao")])
    chat = _ScriptedChat(None)

    with pytest.raises(LLMInvocationError):
        await _agent(chat, service).run("question")


async def test_unparsed_dict_output_becomes_clean_domain_error() -> None:
    service = _StubService([_match("dao")])
    chat = _ScriptedChat({"answer": "ok", "sufficient": True})

    with pytest.raises(LLMInvocationError):
        await _agent(chat, service).run("question")
