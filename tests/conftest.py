import tempfile
import json
import pytest
from pathlib import Path

from src.graph_engine import GraphEngine


@pytest.fixture
def tmp_db():
    with tempfile.NamedTemporaryFile(suffix=".db", delete=False) as f:
        db_path = f.name
    yield db_path
    Path(db_path).unlink(missing_ok=True)


@pytest.fixture
def graph_engine(tmp_db):
    engine = GraphEngine(tmp_db)
    engine.build()
    yield engine
    engine._conn.close()


@pytest.fixture
def sample_yaml(tmp_path):
    data = {
        "category_map": {
            "cables": {
                "subcategories": {
                    "power_cables": {
                        "keywords": ["ВВГ", "NYM", "кабель"],
                        "sites": [
                            {"site": "tinko.ru", "priority": "primary"},
                            {"site": "keaz.ru", "priority": "secondary"},
                        ],
                    }
                }
            }
        }
    }
    path = tmp_path / "categories.yaml"
    import yaml
    with open(path, "w", encoding="utf-8") as f:
        yaml.dump(data, f, allow_unicode=True)
    return str(path)


@pytest.fixture
def sample_approach():
    return {
        "product_type_id": "cables",
        "site_id": "tinko.ru",
        "pattern": [{"action": "navigate", "configurable": False}],
        "concrete": [{"action": "navigate", "url": "https://tinko.ru/catalog"}],
        "selectors_cache": {},
        "method": "direct",
    }


def llm_response(content: str = "", tool_calls: list | None = None) -> dict:
    msg = {"role": "assistant"}
    if content:
        msg["content"] = content
    if tool_calls:
        msg["tool_calls"] = tool_calls
    return {"choices": [{"message": msg}]}


def llm_tool_call(name: str, args: dict, call_id: str = "call_1") -> dict:
    return {
        "id": call_id,
        "function": {"name": name, "arguments": json.dumps(args)},
    }
