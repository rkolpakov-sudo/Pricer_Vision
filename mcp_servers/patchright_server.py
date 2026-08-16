"""MCP server wrapping patchright (undetected Playwright).

Дроп-ин замена @playwright/mcp с CDP-level anti-detection.
patchright проходит Cloudflare, DDoS-Guard, Akamai, Kasada, Datadome.
"""

import asyncio
import json
import logging
import re
import sys
from typing import Any

from mcp.server.lowlevel import Server
from mcp.server.stdio import stdio_server
from mcp import types
from patchright.async_api import async_playwright

logging.basicConfig(level=logging.INFO, format="%(name)s %(message)s")
logger = logging.getLogger("patchright")

_playwright: Any = None
_browser: Any = None
_context: Any = None
_page: Any = None
_element_cache: dict[str, Any] = {}
_console_logs: list[str] = []
_network_requests: list[dict] = []


def _find_chrome() -> str | None:
    import subprocess, os, shutil
    # 1. Playwright-managed Chromium (any version)
    pw_dir = os.path.join(os.environ.get("LOCALAPPDATA", ""), "ms-playwright")
    if os.path.isdir(pw_dir):
        for d in sorted(os.listdir(pw_dir), reverse=True):
            if d.startswith("chromium-"):
                exe = os.path.join(pw_dir, d, "chrome-win64", "chrome.exe")
                if os.path.isfile(exe):
                    return exe
                exe = os.path.join(pw_dir, d, "chrome-win", "chrome.exe")
                if os.path.isfile(exe):
                    return exe
    # 2. System Chrome (check registry)
    try:
        import winreg
        with winreg.OpenKey(winreg.HKEY_LOCAL_MACHINE, r"SOFTWARE\Microsoft\Windows\CurrentVersion\App Paths\chrome.exe") as key:
            path = winreg.QueryValue(key, "")
            if path and os.path.isfile(path):
                return path
    except Exception:
        pass
    # 3. Common paths
    for p in [
        r"C:\Program Files\Google\Chrome\Application\chrome.exe",
        r"C:\Program Files (x86)\Google\Chrome\Application\chrome.exe",
        os.path.expanduser("~\\AppData\\Local\\Google\\Chrome\\Application\\chrome.exe"),
    ]:
        if os.path.isfile(p):
            return p
    # 4. PATH
    return shutil.which("chrome") or shutil.which("google-chrome")

server = Server("pricer-browser")


async def _ensure_page():
    global _playwright, _browser, _context, _page
    alive = False
    try:
        if _page:
            await _page.evaluate("1")
            alive = True
    except Exception:
        _page = None
    if alive:
        return
    if _playwright is None:
        _playwright = await async_playwright().start()
    if _browser is None:
        chrome_path = _find_chrome()
        launch_kwargs = dict(
            headless=False,
            args=[
                "--no-sandbox",
                "--disable-session-crashed-bubble",
                "--disable-infobars",
                "--disable-notifications",
                "--no-first-run",
                "--disable-default-apps",
                "--hide-scrollbars",
            ],
        )
        if chrome_path:
            launch_kwargs["executable_path"] = chrome_path
            logger.info("Using Chrome: %s", chrome_path)
        _browser = await _playwright.chromium.launch(**launch_kwargs)
    if _context is None:
        _context = await _browser.new_context(
            viewport={"width": 1920, "height": 1080},
            user_agent="Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/134.0.0.0 Safari/537.36",
            locale="ru-RU",
            timezone_id="Europe/Moscow",
        )
        _context.on("page", lambda p: p.on("console", lambda msg: _console_logs.append(f"[{msg.type}] {msg.text}")))
    _page = await _context.new_page()
    _page.on("console", lambda msg: _console_logs.append(f"[{msg.type}] {msg.text}"))
    _page.on("response", lambda resp: _network_requests.append({"url": resp.url, "status": resp.status, "method": resp.request.method}))


_DOM_SCAN_SCRIPT = """
(() => {
  const results = [];
  const seen = new Set();
  const all = document.querySelectorAll('a, button, input, select, textarea, h1, h2, h3, h4, h5, h6, label, span, p, li, td, th, [role], [onclick]');
  for (const el of all) {
    if (results.length >= 300) break;
    const tag = el.tagName.toLowerCase();
    if (tag === 'input' && (el.type === 'hidden' || el.type === 'submit' || el.type === 'button')) continue;
    const rect = el.getBoundingClientRect();
    if (rect.width < 5 || rect.height < 5) continue;
    const text = (el.textContent || '').trim().slice(0, 80);
    const ariaLabel = el.getAttribute('aria-label') || '';
    const placeholder = el.getAttribute('placeholder') || '';
    const name = text || ariaLabel || placeholder || '';
    const role = el.getAttribute('role') || tag;
    const uid = tag + '_' + Math.round(rect.left) + '_' + Math.round(rect.top);
    if (seen.has(uid)) continue;
    seen.add(uid);
    results.push({ role, name, tag, href: el.getAttribute('href') || '', type: el.type || '' });
  }
  return JSON.stringify(results);
})();
"""


async def _accessibility_tree(page=None, max_elements=200) -> str:
    p = page or _page
    try:
        url = await p.evaluate("window.location.href")
        title = await p.evaluate("document.title")
    except Exception:
        url, title = "", ""
    header = f"### Page\n- Page URL: {url}\n- Page Title: {title}\n"

    # DOM-based extraction (works on all sites, including SPAs)
    try:
        raw = await p.evaluate(_DOM_SCAN_SCRIPT)
        elements = json.loads(raw) if isinstance(raw, str) else raw
    except Exception:
        elements = []

    if not elements:
        # Fallback: full page text
        try:
            body_text = (await p.evaluate("document.body?.innerText || ''"))[:2000]
            return header + body_text if body_text else header + "(empty page)"
        except Exception:
            return header + "(empty page)"

    lines = []
    for el in elements[:max_elements]:
        role = el.get("role", "")
        name = el.get("name", "")[:80]
        ref = f"e{abs(hash(str(el))) % 1000000}"
        _element_cache[ref] = el

        display_role = role
        if role in ("a", "link"):
            display_role = "link"
        elif role in ("button", "btn"):
            display_role = "button"
        elif role in ("textbox", "searchbox", "input"):
            display_role = "textbox" if el.get("type") != "search" else "searchbox"

        if not name:
            continue

        if display_role in ("button", "link", "textbox", "searchbox", "heading",
                             "combobox", "listbox", "option", "checkbox", "radio",
                             "tab", "menu", "menuitem"):
            lines.append(f'  - [{ref}] {display_role} "{name}"')
        else:
            lines.append(f'  - {display_role} "{name}"')

    return header + "\n".join(lines) if lines else header + "(no interactive elements)"


async def _ref_to_role_locator(ref: str):
    node = _element_cache.get(ref)
    if node and isinstance(node, dict):
        role = node.get("role", "")
        name = node.get("name", "")
        if role and name:
            return role, name
    return None, None


# ── Tool definitions ──

TOOL_DEFS = [
    types.Tool(name="browser_navigate", description="Navigate to URL. Returns accessibility tree.",
               inputSchema={"type": "object", "properties": {"url": {"type": "string"}}, "required": ["url"]}),
    types.Tool(name="browser_snapshot", description="Get accessibility tree snapshot of current page.",
               inputSchema={"type": "object", "properties": {}}),
    types.Tool(name="browser_click", description="Click element by CSS selector or Playwright role locator.",
               inputSchema={"type": "object", "properties": {"target": {"type": "string"}}, "required": ["target"]}),
    types.Tool(name="browser_type", description="Type text into input field.",
               inputSchema={"type": "object", "properties": {"target": {"type": "string"}, "text": {"type": "string"}}, "required": ["target", "text"]}),
    types.Tool(name="browser_press_key", description="Press keyboard key: Enter, Escape, ArrowDown, ArrowUp, Tab, Backspace.",
               inputSchema={"type": "object", "properties": {"key": {"type": "string"}}, "required": ["key"]}),
    types.Tool(name="browser_wait_for", description="Wait for ms milliseconds.",
               inputSchema={"type": "object", "properties": {"ms": {"type": "integer", "default": 1000}}}),
    types.Tool(name="browser_evaluate", description="Execute JavaScript in page context.",
               inputSchema={"type": "object", "properties": {"function": {"type": "string"}}, "required": ["function"]}),
    types.Tool(name="browser_run_code_unsafe", description="Execute arbitrary JavaScript.",
               inputSchema={"type": "object", "properties": {"code": {"type": "string"}}, "required": ["code"]}),
    types.Tool(name="browser_take_screenshot", description="Take a screenshot.",
               inputSchema={"type": "object", "properties": {}}),
    types.Tool(name="browser_close", description="Close current page.",
               inputSchema={"type": "object", "properties": {}}),
    types.Tool(name="browser_resize", description="Resize viewport.",
               inputSchema={"type": "object", "properties": {"width": {"type": "integer", "default": 1920}, "height": {"type": "integer", "default": 1080}}}),
    types.Tool(name="browser_console_messages", description="Get console messages.",
               inputSchema={"type": "object", "properties": {}}),
    types.Tool(name="browser_handle_dialog", description="Handle dialog: accept or dismiss.",
               inputSchema={"type": "object", "properties": {"action": {"type": "string", "default": "accept"}}}),
    types.Tool(name="browser_file_upload", description="Upload files.",
               inputSchema={"type": "object", "properties": {"selector": {"type": "string"}, "files": {"type": "array", "items": {"type": "string"}}}, "required": ["selector", "files"]}),
    types.Tool(name="browser_drag", description="Drag element to target.",
               inputSchema={"type": "object", "properties": {"source": {"type": "string"}, "target": {"type": "string"}}, "required": ["source", "target"]}),
    types.Tool(name="browser_fill_form", description="Fill form with JSON values.",
               inputSchema={"type": "object", "properties": {"selector": {"type": "string"}, "values": {"type": "string"}}, "required": ["selector", "values"]}),
    types.Tool(name="browser_navigate_back", description="Go back one page.",
               inputSchema={"type": "object", "properties": {}}),
    types.Tool(name="browser_network_requests", description="Get network requests.",
               inputSchema={"type": "object", "properties": {}}),
    types.Tool(name="browser_network_request", description="Get specific request by URL.",
               inputSchema={"type": "object", "properties": {"url": {"type": "string"}}, "required": ["url"]}),
    types.Tool(name="browser_tabs", description="List all tabs.",
               inputSchema={"type": "object", "properties": {}}),
    types.Tool(name="browser_hover", description="Hover over element.",
               inputSchema={"type": "object", "properties": {"target": {"type": "string"}}, "required": ["target"]}),
    types.Tool(name="browser_select_option", description="Select option from dropdown.",
               inputSchema={"type": "object", "properties": {"selector": {"type": "string"}, "values": {"type": "string"}}, "required": ["selector", "values"]}),
    types.Tool(name="browser_drop", description="Drop element onto target.",
               inputSchema={"type": "object", "properties": {"source": {"type": "string"}, "target": {"type": "string"}}, "required": ["source", "target"]}),
]


@server.list_tools()
async def handle_list_tools() -> list[types.Tool]:
    return TOOL_DEFS


@server.call_tool()
async def handle_call_tool(name: str, arguments: dict) -> list[types.TextContent]:
    await _ensure_page()
    result = await _dispatch(name, arguments)
    return [types.TextContent(type="text", text=result)]


async def _dispatch(name: str, args: dict) -> str:
    global _page
    try:
        if name == "browser_navigate":
            url = args.get("url", "")
            try:
                await _page.goto(url, wait_until="domcontentloaded", timeout=30000)
                await asyncio.sleep(1.0)
            except Exception as e:
                return f"error: navigation failed: {e}"
            return await _accessibility_tree()

        elif name == "browser_snapshot":
            return await _accessibility_tree()

        elif name == "browser_click":
            target = args.get("target", "")
            return await _do_click(target)

        elif name == "browser_type":
            target = args.get("target", "")
            text = args.get("text", "")
            return await _do_type(target, text)

        elif name == "browser_press_key":
            key = args.get("key", "")
            valid = {"Enter", "Escape", "ArrowDown", "ArrowUp", "Tab", "Backspace"}
            if key not in valid:
                return f"error: invalid key. Valid: {', '.join(sorted(valid))}"
            await _page.keyboard.press(key)
            await asyncio.sleep(0.2)
            return f"Pressed {key}"

        elif name == "browser_wait_for":
            ms = max(100, min(int(args.get("ms", 1000)), 30000))
            await asyncio.sleep(ms / 1000)
            return f"Waited for {ms//1000}"

        elif name in ("browser_evaluate", "browser_run_code_unsafe"):
            code = args.get("function") or args.get("code", "")
            try:
                result = await _page.evaluate(code)
                if result is None:
                    return "null"
                if isinstance(result, (dict, list)):
                    return json.dumps(result, ensure_ascii=False)[:50000]
                return str(result)[:50000]
            except Exception as e:
                return f"error: {e}"

        elif name == "browser_take_screenshot":
            import tempfile
            path = tempfile.mktemp(suffix=".png")
            try:
                await _page.screenshot(path=path, full_page=False)
                return f"Screenshot saved to {path}"
            except Exception as e:
                return f"error: screenshot failed: {e}"

        elif name == "browser_close":
            try:
                await _page.close()
            except Exception:
                pass
            _page = None
            return "ok"

        elif name == "browser_resize":
            w = int(args.get("width", 1920))
            h = int(args.get("height", 1080))
            await _page.set_viewport_size({"width": w, "height": h})
            return f"ok (resized to {w}x{h})"

        elif name == "browser_console_messages":
            msgs = list(_console_logs)
            return "\n".join(msgs[-50:]) if msgs else "(no console messages)"

        elif name == "browser_handle_dialog":
            action = args.get("action", "accept")
            try:
                dialog = await _page.wait_for_event("dialog", timeout=5000)
                await (dialog.accept() if action == "accept" else dialog.dismiss())
                return f"Dialog {action}ed"
            except Exception as e:
                return f"error: no dialog: {e}"

        elif name == "browser_file_upload":
            sel = args.get("selector", "")
            files = args.get("files", [])
            await _page.locator(sel).set_input_files(files)
            return "ok"

        elif name in ("browser_drag", "browser_drop"):
            src = args.get("source", "")
            tgt = args.get("target", "")
            await _page.locator(src).drag_to(_page.locator(tgt))
            return "ok"

        elif name == "browser_fill_form":
            sel = args.get("selector", "")
            vals = json.loads(args.get("values", "{}"))
            for field, value in vals.items():
                loc = _page.locator(f"{sel} {field}" if field else sel)
                await loc.fill(str(value))
            return "ok"

        elif name == "browser_navigate_back":
            await _page.go_back()
            return "ok"

        elif name == "browser_network_requests":
            return json.dumps(_network_requests[-100:], ensure_ascii=False) if _network_requests else "(no requests)"

        elif name == "browser_network_request":
            url = args.get("url", "")
            for r in _network_requests:
                if url in r.get("url", ""):
                    return json.dumps(r, ensure_ascii=False)
            return f"error: request not found: {url}"

        elif name == "browser_tabs":
            try:
                pages = _context.pages
                lines = []
                for i, p in enumerate(pages):
                    try:
                        title = await p.evaluate("document.title")
                        url = await p.evaluate("window.location.href")
                        cur = " (current)" if p == _page else ""
                        lines.append(f"  {i}: [{title[:60]}]({url[:80]}){cur}")
                    except Exception:
                        lines.append(f"  {i}: (unavailable)")
                return "\n".join(lines) if lines else "(no tabs)"
            except Exception as e:
                return f"error: {e}"

        elif name == "browser_hover":
            target = args.get("target", "")
            try:
                await _page.hover(target, timeout=10000)
                return "ok"
            except Exception as e:
                return f"error: hover failed: {e}"

        elif name == "browser_select_option":
            sel = args.get("selector", "")
            vals = json.loads(args.get("values", "[]"))
            await _page.select_option(sel, vals)
            return "ok"

        return f"error: unknown tool {name}"
    except Exception as e:
        logger.exception("Tool %s failed", name)
        return f"error: {e}"


async def _do_click(target: str) -> str:
    # Try ref lookup
    role, name = await _ref_to_role_locator(target)
    if role and name:
        try:
            await _page.get_by_role(role, name=name).click()
            await asyncio.sleep(0.3)
            return "ok"
        except Exception:
            pass
    # Try role locator string
    if target.startswith(("link ", "button ", "textbox ", "searchbox ", "heading ")):
        parts = target.split(" ", 1)
        if len(parts) == 2:
            try:
                await _page.get_by_role(parts[0], name=parts[1]).click()
                await asyncio.sleep(0.3)
                return "ok"
            except Exception:
                pass
    # Fallback to CSS selector
    try:
        await _page.click(target, timeout=10000)
        await asyncio.sleep(0.3)
        return "ok"
    except Exception as e:
        return f"error: click failed: {e}"


async def _do_type(target: str, text: str) -> str:
    role, name = await _ref_to_role_locator(target)
    if role and name:
        try:
            await _page.get_by_role(role, name=name).fill(text)
            return "ok"
        except Exception:
            pass
    if target.startswith(("textbox ", "searchbox ")):
        parts = target.split(" ", 1)
        if len(parts) == 2:
            try:
                await _page.get_by_role(parts[0], name=parts[1]).fill(text)
                return "ok"
            except Exception:
                pass
    # Fallback to CSS locator
    try:
        locator = _page.locator(target)
        await locator.fill(text, timeout=10000)
        return "ok"
    except Exception as e:
        return f"error: type failed: {e}"


async def main():
    async with stdio_server() as (read_stream, write_stream):
        await server.run(read_stream, write_stream, server.create_initialization_options())

if __name__ == "__main__":
    asyncio.run(main())
