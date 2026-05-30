"""Shared helpers for provider-native structured output.

Each client constrains the model to a Pydantic schema with its provider's
native mechanism, then validates the result here so errors are consistent
across providers.
"""

from typing import Any, cast

from pydantic import BaseModel, ValidationError


class StructuredOutputError(RuntimeError):
    """Raised when a model's reply cannot be validated against the schema."""


def parse_json[ModelT: BaseModel](content: str, schema: type[ModelT], provider: str) -> ModelT:
    """Validate a JSON string into ``schema`` (for providers that return text)."""
    try:
        return schema.model_validate_json(content)
    except ValidationError as exc:
        raise StructuredOutputError(
            f"{provider} returned data that does not match {schema.__name__}: {exc}"
        ) from exc


def parse_obj[ModelT: BaseModel](data: Any, schema: type[ModelT], provider: str) -> ModelT:
    """Validate an already-parsed object into ``schema`` (e.g. Anthropic tool input)."""
    try:
        return schema.model_validate(data)
    except ValidationError as exc:
        raise StructuredOutputError(
            f"{provider} returned data that does not match {schema.__name__}: {exc}"
        ) from exc


def openai_strict_schema(schema: type[BaseModel]) -> dict[str, Any]:
    """Convert a Pydantic JSON Schema into OpenAI's strict format.

    Strict mode requires every object to set additionalProperties to false and
    to list all properties as required, and it rejects default. vLLM and
    OpenRouter accept the same shape, so one transform covers every
    OpenAI-compatible endpoint.
    """
    return cast("dict[str, Any]", _strictify(schema.model_json_schema()))


def _strictify(node: Any) -> Any:
    if isinstance(node, dict):
        result = {key: _strictify(value) for key, value in node.items() if key != "default"}
        if result.get("type") == "object" and "properties" in result:
            result["additionalProperties"] = False
            result["required"] = list(result["properties"].keys())
        return result
    if isinstance(node, list):
        return [_strictify(item) for item in node]
    return node
