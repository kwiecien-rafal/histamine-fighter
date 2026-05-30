from app.llm.mock_client import MockLLMClient
from app.schemas.meal import DishVerdict


async def test_generate_structured_returns_valid_instance() -> None:
    client = MockLLMClient()

    result = await client.generate_structured("system", "Pizza", DishVerdict)

    assert isinstance(result, DishVerdict)
    assert result.verdict in ("safe", "depends", "avoid")
    assert isinstance(result.dish, str) and result.dish
