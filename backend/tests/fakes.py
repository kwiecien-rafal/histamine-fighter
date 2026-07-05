"""Deterministic test doubles that avoid loading the real embedding model,
touching real quota transactions, or making outbound HTTP calls."""

import hashlib
import math
import re
from datetime import UTC, datetime, timedelta
from uuid import UUID

import httpx

from app.embeddings import EMBEDDING_DIM
from app.embeddings.base import Embedder
from app.services.quota_service import QuotaExceededError, QuotaStatus

_TOKEN = re.compile(r"[a-z0-9]+")


class FakeQuotaService:
    """An in-memory stand-in for QuotaService.

    The real service commits in its own transactions, which would escape the
    test suite's rollback isolation; endpoint tests use this double and the
    real SQL is covered by test_quota_service.py directly.
    """

    def __init__(self) -> None:
        self.shared_charges: list[tuple[UUID, str]] = []
        self.signup_charges: list[str] = []
        self.send_charges: list[str] = []
        self.shared_error: QuotaExceededError | None = None
        self.signup_error: QuotaExceededError | None = None
        self.send_error: QuotaExceededError | None = None
        self.status = QuotaStatus(
            used=0, limit=20, resets_at=datetime.now(UTC) + timedelta(hours=1)
        )

    async def charge_shared(self, user_id: UUID, ip: str) -> None:
        if self.shared_error is not None:
            raise self.shared_error
        self.shared_charges.append((user_id, ip))

    async def charge_signup(self, ip: str) -> None:
        if self.signup_error is not None:
            raise self.signup_error
        self.signup_charges.append(ip)

    async def charge_magic_send(self, ip: str) -> None:
        if self.send_error is not None:
            raise self.send_error
        self.send_charges.append(ip)

    async def read_status(self, user_id: UUID, session: object) -> QuotaStatus:
        return self.status


def quota_exhausted(scope: str = "user", *, limit: int = 20) -> QuotaExceededError:
    """A ready-made exhaustion error for the fake to raise."""
    return QuotaExceededError(
        scope,  # type: ignore[arg-type]
        used=limit,
        limit=limit,
        resets_at=datetime.now(UTC) + timedelta(hours=1),
    )


def _refuse_outbound(request: httpx.Request) -> httpx.Response:
    raise AssertionError(f"test made an unexpected outbound HTTP call: {request.url}")


def refusing_http_client() -> httpx.AsyncClient:
    """An httpx client that fails the test on any use.

    The default for the suite: Turnstile and Resend are unconfigured in tests,
    so nothing should go out. OAuth tests replace it with a scripted transport.
    """
    return httpx.AsyncClient(transport=httpx.MockTransport(_refuse_outbound))


class FakeEmbedder(Embedder):
    """A bag-of-words embedder: hashes each word into a dimension, L2-normalizes.

    Deterministic and offline, so the fast suite never downloads a model. Cosine
    similarity tracks word overlap, which is enough to exercise retrieval order.
    """

    def __init__(self, dim: int = EMBEDDING_DIM) -> None:
        self._dim = dim

    @property
    def dimension(self) -> int:
        return self._dim

    @property
    def model_name(self) -> str:
        return "fake/deterministic"

    async def embed_documents(self, texts: list[str]) -> list[list[float]]:
        return [self._vector(text) for text in texts]

    async def embed_query(self, text: str) -> list[float]:
        return self._vector(text)

    def _vector(self, text: str) -> list[float]:
        vector = [0.0] * self._dim
        for token in _TOKEN.findall(text.lower()):
            digest = hashlib.blake2b(token.encode(), digest_size=8).digest()
            vector[int.from_bytes(digest) % self._dim] += 1.0
        norm = math.sqrt(sum(value * value for value in vector))
        if norm:
            vector = [value / norm for value in vector]
        return vector
