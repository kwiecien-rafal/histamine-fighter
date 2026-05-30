import pytest
from pydantic import BaseModel

from app.llm.structured import (
    StructuredOutputError,
    openai_strict_schema,
    parse_json,
    parse_obj,
)


class _Nested(BaseModel):
    label: str


class _Sample(BaseModel):
    name: str
    count: int
    nested: _Nested


def test_parse_json_valid() -> None:
    result = parse_json('{"name": "x", "count": 1, "nested": {"label": "y"}}', _Sample, "test")
    assert result == _Sample(name="x", count=1, nested=_Nested(label="y"))


def test_parse_json_invalid_raises_structured_error() -> None:
    with pytest.raises(StructuredOutputError):
        parse_json('{"name": "x"}', _Sample, "test")


def test_parse_obj_valid() -> None:
    result = parse_obj({"name": "x", "count": 2, "nested": {"label": "z"}}, _Sample, "test")
    assert result.count == 2


def test_parse_obj_invalid_raises_structured_error() -> None:
    with pytest.raises(StructuredOutputError):
        parse_obj({"name": "x", "count": "not-an-int"}, _Sample, "test")


def test_openai_strict_schema_forces_required_and_no_additional_props() -> None:
    schema = openai_strict_schema(_Sample)

    assert schema["additionalProperties"] is False
    assert sorted(schema["required"]) == ["count", "name", "nested"]

    nested = schema["$defs"]["_Nested"]
    assert nested["additionalProperties"] is False
    assert nested["required"] == ["label"]


def test_openai_strict_schema_drops_default() -> None:
    class WithDefault(BaseModel):
        items: list[str] = []

    schema = openai_strict_schema(WithDefault)

    assert "default" not in schema["properties"]["items"]
    assert schema["required"] == ["items"]
