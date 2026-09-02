import json
import logging

logger = logging.getLogger("pricer.tools")


_KNOWN_TOOL_NAMES = frozenset({
    "browser_navigate", "navigate", "browser_snapshot", "snapshot",
    "browser_click", "click", "browser_type", "type_text",
    "browser_press_key", "press_key", "browser_wait_for", "wait",
    "browser_evaluate", "evaluate", "browser_run_code_unsafe",
    "browser_extract", "browser_take_screenshot", "browser_close",
    "browser_resize", "browser_console_messages", "browser_handle_dialog",
    "browser_file_upload", "browser_drag", "browser_fill_form",
    "browser_navigate_back", "browser_network_requests", "browser_network_request",
    "browser_tabs", "browser_hover", "browser_select_option", "browser_drop",
    "get_approaches", "search_sites", "get_confirmed_prices", "get_hints",
    "save_confirmed_price", "save_approach", "save_discovered_site",
})


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
    if parsed:
        return parsed
    # Fallback: LLM (gemma) возвращает размышления в JSON-блоках без метки
    # TOOL:/RESULT: (например ` ```json {"tool": "...", "arguments": {...}} ``` `).
    # Извлекаем любой JSON с полем tool/name + arguments и без price.
    for js in _extract_json_objects(content):
        try:
            cmd = json.loads(js)
        except json.JSONDecodeError:
            continue
        if not isinstance(cmd, dict) or cmd.get("price") is not None:
            continue
        name = cmd.get("name") or cmd.get("tool") or cmd.get("tool_name") or cmd.get("function", "")
        args = cmd.get("arguments") or cmd.get("args") or cmd.get("parameters")
        if not isinstance(args, dict):
            args = {}
        if name and name in _KNOWN_TOOL_NAMES:
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
