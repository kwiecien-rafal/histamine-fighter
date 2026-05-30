import asyncio
from collections.abc import AsyncIterator
from typing import Any

from pydantic import BaseModel

from app.llm.base import LLMClient


class MockLLMClient(LLMClient):
    """Zero-config client for local dev and tests. Makes no network calls."""

    @property
    def model_name(self) -> str:
        return "mock-llm-v0"

    async def complete(self, system: str, user: str) -> str:
        return _canned_response(user)

    async def stream(self, system: str, user: str) -> AsyncIterator[str]:
        for chunk in _canned_response(user).split(" "):
            await asyncio.sleep(0.02)
            yield chunk + " "

    async def generate_structured[ModelT: BaseModel](
        self, system: str, user: str, schema: type[ModelT]
    ) -> ModelT:
        return schema.model_validate(_example_for(schema))


def _canned_response(user: str) -> str:
    dish = user.strip() or "your dish"
    return (
        f"Mock verdict for '{dish}': this looks risky for histamine intolerance. "
        f"A safer swap would be a fresh herb-based version with skinless chicken "
        f"and no aged cheese."
    )


def _example_for(schema: type[BaseModel]) -> Any:
    """Walk the schema's JSON Schema and build valid placeholder data."""
    root = schema.model_json_schema()
    return _node_example(root, root.get("$defs", {}))


def _node_example(node: dict[str, Any], defs: dict[str, Any]) -> Any:
    if "$ref" in node:
        return _node_example(defs[node["$ref"].split("/")[-1]], defs)
    if "anyOf" in node:
        options = [opt for opt in node["anyOf"] if opt.get("type") != "null"] or node["anyOf"]
        return _node_example(options[0], defs)
    if "enum" in node:
        return node["enum"][0]
    if "const" in node:
        return node["const"]

    node_type = node.get("type")
    if node_type == "object":
        properties: dict[str, Any] = node.get("properties", {})
        return {name: _node_example(sub, defs) for name, sub in properties.items()}
    if node_type == "array":
        return [_node_example(node.get("items", {}), defs)]
    if node_type == "integer":
        return 0
    if node_type == "number":
        return 0.0
    if node_type == "boolean":
        return True
    return node.get("title", "sample")
