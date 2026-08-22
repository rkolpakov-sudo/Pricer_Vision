import json
import logging

logger = logging.getLogger("pricer.tools")


def parse_tool_calls(response: dict) -> list[dict]:
    choice = (response.get("choices") or [{}])[0]
    message = choice.get("message", {})
    tool_calls = message.get("tool_calls", [])

    if isinstance(tool_calls, list) and tool_calls:
        parsed = []
        for tc in tool_calls:
            if not isinstance(tc, dict):
                continue
            func = tc.get("function", {})
            if not isinstance(func, dict):
                continue
            try:
                args = json.loads(func.get("arguments", "{}"))
            except json.JSONDecodeError:
                logger.warning(f"parse_tool_calls: bad JSON: {func.get('arguments', '')[:200]}")
                args = {}
            parsed.append({
                "name": func.get("name", ""),
                "arguments": args,
                "id": tc.get("id", ""),
            })
        if parsed:
            return parsed

    fc = message.get("function_call")
    if isinstance(fc, dict):
        try:
            args = json.loads(fc.get("arguments", "{}"))
        except json.JSONDecodeError:
            logger.warning(f"parse_tool_calls: bad function_call JSON: {fc.get('arguments', '')[:200]}")
            args = {}
        return [{
            "name": fc.get("name", ""),
            "arguments": args,
            "id": fc.get("name", ""),
        }]

    return []


def _extract_json_objects(text: str) -> list[str]:
    """Extract top-level JSON objects from text, handling nested braces."""
    results = []
    i = 0
    while i < len(text):
        start = text.find("{", i)
        if start == -1:
            break
        depth = 0
        for j in range(start, len(text)):
            if text[j] == "{":
                depth += 1
            elif text[j] == "}":
                depth -= 1
                if depth == 0:
                    candidate = text[start:j+1]
                    try:
                        json.loads(candidate)
                        results.append(candidate)
                    except json.JSONDecodeError:
                        pass
                    i = j + 1
                    break
        else:
            i = start + 1
    return results


def _find_labeled_json(text: str, label: str) -> list[str]:
    """Find JSON objects prefixed with a label like TOOL: or RESULT:"""
    results = []
    for js in _extract_json_objects(text):
        pos = text.find(js)
        prefix = text[max(0, pos - 20):pos].strip()
        if prefix.endswith(label):
            results.append(js)
    return results


def parse_text_tools(content: str) -> list[dict]:
    if not content:
        return []
    parsed = []
    for js in _find_labeled_json(content, "TOOL"):
        try:
            cmd = json.loads(js)
        except json.JSONDecodeError:
            continue
        name = cmd.get("name") or cmd.get("tool", "")
        args = cmd.get("arguments", {})
        if not isinstance(args, dict):
            args = {}
        if name:
            parsed.append({"name": name, "arguments": args, "id": name})
    return parsed


def parse_text_result(content: str) -> dict | None:
    if not content:
        return None
    for js in _find_labeled_json(content, "RESULT"):
        try:
            result = json.loads(js)
            if result.get("price") is not None:
                return result
        except json.JSONDecodeError:
            continue
    return None


def _extract_first_json(text: str) -> dict | None:
    start = text.find("{")
    if start == -1:
        return None

    depth = 0
    for i in range(start, len(text)):
        if text[i] == "{":
            depth += 1
        elif text[i] == "}":
            depth -= 1
            if depth == 0:
                try:
                    return json.loads(text[start:i+1])
                except json.JSONDecodeError:
                    return None
    return None


def parse_final_response(response: dict) -> dict:
    choice = (response.get("choices") or [{}])[0]
    message = choice.get("message", {})
    content = message.get("content", "")

    if content:
        labeled = parse_text_result(content)
        if labeled and labeled.get("price") is not None:
            return labeled

        result = _extract_first_json(content)
        if result and result.get("price") is not None:
            return {
                "price": result.get("price"),
                "confidence": float(result.get("confidence", 0.5)),
                "url": result.get("url", ""),
                "site": result.get("site", ""),
                "reason": result.get("reason", ""),
                "requires_review": result.get("requires_review", True),
                "alternative_sites": result.get("alternative_sites", []),
            }

    return {
        "price": None,
        "confidence": 0.0,
        "url": "",
        "site": "",
        "reason": content[:500] if content else "Empty response",
        "requires_review": True,
    }
