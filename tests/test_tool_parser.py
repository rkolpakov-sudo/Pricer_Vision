import json
import pytest
from src.tool_parser import parse_tool_calls, parse_final_response
from tests.conftest import llm_response, llm_tool_call


class TestParseToolCalls:
    def test_empty_response(self):
        assert parse_tool_calls({}) == []

    def test_no_tool_calls(self):
        resp = llm_response(content="just text")
        assert parse_tool_calls(resp) == []

    def test_single_tool_call(self):
        resp = llm_response(tool_calls=[
            llm_tool_call("navigate", {"url": "https://example.com"})
        ])
        result = parse_tool_calls(resp)
        assert len(result) == 1
        assert result[0]["name"] == "navigate"
        assert result[0]["arguments"] == {"url": "https://example.com"}

    def test_multiple_tool_calls(self):
        resp = llm_response(tool_calls=[
            llm_tool_call("snapshot", {}, "call_1"),
            llm_tool_call("click", {"element": "a.button"}, "call_2"),
        ])
        result = parse_tool_calls(resp)
        assert len(result) == 2
        assert result[0]["name"] == "snapshot"
        assert result[1]["name"] == "click"

    def test_bad_json_arguments(self):
        tc = {
            "id": "call_1",
            "function": {"name": "navigate", "arguments": "not json at all"},
        }
        resp = llm_response(tool_calls=[tc])
        result = parse_tool_calls(resp)
        assert result[0]["arguments"] == {}

    def test_string_instead_of_dict(self):
        resp = {"choices": [{"message": {"tool_calls": "string"}}]}
        assert parse_tool_calls(resp) == []

    def test_nested_args(self):
        resp = llm_response(tool_calls=[
            llm_tool_call("save_approach", {
                "product_type": "cables", "steps": [{"action": "click"}]
            })
        ])
        result = parse_tool_calls(resp)
        assert result[0]["arguments"]["steps"][0]["action"] == "click"


class TestParseFinalResponse:
    def test_simple_json(self):
        resp = llm_response(content='{"price": 1500.50, "confidence": 0.95}')
        result = parse_final_response(resp)
        assert result["price"] == 1500.50
        assert result["confidence"] == 0.95

    def test_json_in_markdown(self):
        resp = llm_response(content="```json\n{\"price\": 1200}\n```")
        result = parse_final_response(resp)
        assert result["price"] == 1200

    def test_no_price(self):
        resp = llm_response(content='{"price": null, "reason": "not found"}')
        result = parse_final_response(resp)
        assert result["price"] is None
        assert result["requires_review"] is True

    def test_empty_content(self):
        resp = llm_response(content="")
        result = parse_final_response(resp)
        assert result["price"] is None

    def test_no_choice(self):
        assert parse_final_response({})["price"] is None

    def test_partial_result(self):
        resp = llm_response(content='{"price": 500, "url": "http://example.com"}')
        result = parse_final_response(resp)
        assert result["price"] == 500
        assert result["confidence"] == 0.5

    def test_alternative_sites(self):
        resp = llm_response(
            content='{"price": 100, "alternative_sites": ["site1.ru", "site2.ru"]}'
        )
        result = parse_final_response(resp)
        assert "site1.ru" in result["alternative_sites"]
