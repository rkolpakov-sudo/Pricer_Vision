"""MCP server exposing an anti-detect browser backend (camoufox | nodriver).

Drop-in replacement for @playwright/mcp with the same tool names so the agent
prompt, graph engine and study runner keep working unchanged.

Backends (chosen via --backend):
  camoufox   — Firefox fork with C++-level fingerprint spoofing + real-world
               fingerprint presets (default). Passes hcheck/Qrator challenges
               that flag the plain Chrome fingerprint.
  nodriver   — undetected CDP driver on real Chrome (third fallback).

Usage:
  python browser_server.py --backend camoufox [--headless]
  python browser_server.py --backend nodriver  [--headless]
"""

import argparse
import asyncio
import json
import logging
import os
import sys
import tempfile

from mcp.server.lowlevel import Server
from mcp.server.stdio import stdio_server
from mcp import types

logging.basicConfig(level=logging.INFO, format="%(name)s %(message)s")
logger = logging.getLogger("pricer.browser")


async def _handle_list_tools(ctx, params) -> types.ListToolsResult:
    return types.ListToolsResult(tools=TOOL_DEFS)


async def _handle_call_tool(ctx, params) -> types.CallToolResult:
    if _driver._browser is None:
        await _driver.start()
    result = await _dispatch(params.name, params.arguments or {})
    return types.CallToolResult(content=[types.TextContent(type="text", text=result)])


async def _handle_call_tool_v1(name: str, arguments: dict) -> list[types.TextContent]:
    if _driver._browser is None:
        await _driver.start()
    result = await _dispatch(name, arguments or {})
    return [types.TextContent(type="text", text=result)]


def _build_server():
    """Create the lowlevel MCP server.

    mcp >= 2.0 accepts on_list_tools/on_call_tool in the constructor; older mcp
    (1.x, e.g. 1.28.1) registers handlers via decorators. Support both so the
    server runs under whatever venv launches it.
    """
    try:
        return Server(
            "pricer-browser",
            on_list_tools=_handle_list_tools,
            on_call_tool=_handle_call_tool,
        )
    except TypeError:
        srv = Server("pricer-browser")

        @srv.list_tools()
        async def _list_tools_v1():
            return TOOL_DEFS

        @srv.call_tool(validate_input=False)
        async def _call_tool_v1(name: str, arguments: dict):
            return await _handle_call_tool_v1(name, arguments)

        return srv


server = _build_server()

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

TOOL_DEFS = [
    types.Tool(name="browser_navigate", description="Navigate to URL. Returns accessibility tree.",
               inputSchema={"type": "object", "properties": {"url": {"type": "string"}}, "required": ["url"]}),
    types.Tool(name="browser_snapshot", description="Get accessibility tree snapshot of current page.",
               inputSchema={"type": "object", "properties": {}}),
    types.Tool(name="browser_click", description="Click element by CSS selector, Playwright role locator, or snapshot ref.",
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
    types.Tool(name="browser_tabs", description="List all tabs (optional index switches tab).",
               inputSchema={"type": "object", "properties": {"index": {"type": "integer"}}}),
    types.Tool(name="browser_hover", description="Hover over element.",
               inputSchema={"type": "object", "properties": {"target": {"type": "string"}}, "required": ["target"]}),
    types.Tool(name="browser_select_option", description="Select option from dropdown.",
               inputSchema={"type": "object", "properties": {"selector": {"type": "string"}, "values": {"type": "string"}}, "required": ["selector", "values"]}),
    types.Tool(name="browser_drop", description="Drop element onto target.",
               inputSchema={"type": "object", "properties": {"source": {"type": "string"}, "target": {"type": "string"}}, "required": ["source", "target"]}),
]

_element_cache: dict[str, dict] = {}


def _ref_to_role_locator(ref: str):
    node = _element_cache.get(ref)
    if node and isinstance(node, dict) and node.get("role") and node.get("name"):
        return node["role"], node["name"]
    return None, None


class BaseDriver:
    name = "base"

    def __init__(self, headless: bool = False):
        self.headless = headless
        self._pages: list = []
        self._page = None
        self._console_logs: list[str] = []
        self._network_requests: list[dict] = []

    async def start(self):
        raise NotImplementedError

    async def stop(self):
        raise NotImplementedError

    async def new_page(self):
        raise NotImplementedError

    async def current_page(self):
        if self._page is None:
            self._page = await self.new_page()
        return self._page

    async def close_page(self, page) -> str:
        if page in self._pages:
            self._pages.remove(page)
        try:
            await self._close_page_impl(page)
        except Exception:
            pass
        if self._page == page:
            self._page = self._pages[-1] if self._pages else None
        return "ok"

    async def _close_page_impl(self, page):
        raise NotImplementedError


class CamoufoxDriver(BaseDriver):
    """Firefox fork: C++-level fingerprint spoofing, real fingerprint presets."""

    name = "camoufox"

    def __init__(self, headless: bool = False):
        super().__init__(headless)
        self._cam = None
        self._browser = None

    async def start(self):
        from camoufox.async_api import AsyncCamoufox
        last = None
        for attempt in range(4):
            self._cam = AsyncCamoufox(
                headless=self.headless,
                os="windows",
                fingerprint_preset=True,
                humanize=True,
            )
            try:
                self._browser = await self._cam.start()
                self._page = await self.new_page()
                return
            except Exception as e:
                last = e
                logger.warning("camoufox start attempt %d failed: %s", attempt + 1, e)
                try:
                    await self._cam.__aexit__(None, None, None)
                except Exception:
                    pass
                self._cam = None
                self._browser = None
        raise RuntimeError(f"camoufox start failed after retries: {last}")

    async def stop(self):
        if self._cam is not None:
            try:
                await self._cam.__aexit__(None, None, None)
            except Exception:
                pass
        self._cam = None
        self._browser = None
        self._pages = []
        self._page = None

    async def new_page(self):
        page = await self._browser.new_page()
        page.on("console", lambda msg: self._console_logs.append(f"[{msg.type}] {msg.text}"))
        page.on("response", lambda resp: self._network_requests.append(
            {"url": resp.url, "status": resp.status, "method": resp.request.method}))
        self._pages.append(page)
        self._page = page
        return page

    async def _close_page_impl(self, page):
        await page.close()

    async def goto(self, page, url: str, timeout: int = 30000):
        try:
            await page.goto(url, wait_until="domcontentloaded", timeout=timeout)
        except Exception as e:
            return f"error: navigation failed: {e}"
        await asyncio.sleep(1.0)
        return None

    async def evaluate(self, page, js: str) -> str:
        try:
            res = await page.evaluate(js)
        except Exception as e:
            return f"__JS_ERR__: {type(e).__name__}: {str(e)[:150]}"
        if res is None:
            return "null"
        if isinstance(res, (dict, list)):
            return json.dumps(res, ensure_ascii=False)[:50000]
        return str(res)[:50000]

    async def title(self, page) -> str:
        try:
            return await page.title()
        except Exception:
            return ""

    async def url(self, page) -> str:
        try:
            return page.url
        except Exception:
            return ""

    async def click(self, page, target: str) -> str:
        role, name = _ref_to_role_locator(target)
        if role and name:
            try:
                await page.get_by_role(role, name=name).click(timeout=10000)
                await asyncio.sleep(0.3)
                return "ok"
            except Exception:
                pass
        if target.startswith(("link ", "button ", "textbox ", "searchbox ", "heading ")):
            parts = target.split(" ", 1)
            try:
                await page.get_by_role(parts[0], name=parts[1]).click(timeout=10000)
                await asyncio.sleep(0.3)
                return "ok"
            except Exception:
                pass
        try:
            await page.click(target, timeout=10000)
            await asyncio.sleep(0.3)
            return "ok"
        except Exception as e:
            return f"error: click failed: {e}"

    async def type_text(self, page, target: str, text: str) -> str:
        role, name = _ref_to_role_locator(target)
        if role and name:
            try:
                await page.get_by_role(role, name=name).fill(text, timeout=10000)
                return "ok"
            except Exception:
                pass
        if target.startswith(("textbox ", "searchbox ")):
            parts = target.split(" ", 1)
            try:
                await page.get_by_role(parts[0], name=parts[1]).fill(text, timeout=10000)
                return "ok"
            except Exception:
                pass
        try:
            await page.locator(target).fill(text, timeout=10000)
            return "ok"
        except Exception as e:
            return f"error: type failed: {e}"

    async def hover(self, page, target: str) -> str:
        try:
            await page.hover(target, timeout=10000)
            return "ok"
        except Exception as e:
            return f"error: hover failed: {e}"

    async def press_key(self, page, key: str) -> str:
        try:
            await page.keyboard.press(key)
            await asyncio.sleep(0.2)
            return f"Pressed {key}"
        except Exception as e:
            return f"error: {e}"

    async def screenshot(self, page, path: str) -> str:
        try:
            await page.screenshot(path=path, full_page=False)
            return f"Screenshot saved to {path}"
        except Exception as e:
            return f"error: screenshot failed: {e}"

    async def resize(self, page, w: int, h: int) -> str:
        try:
            await page.set_viewport_size({"width": w, "height": h})
            return f"ok (resized to {w}x{h})"
        except Exception as e:
            return f"error: resize failed: {e}"

    async def back(self, page) -> str:
        await page.go_back()
        return "ok"

    async def tabs(self) -> str:
        lines = []
        for i, p in enumerate(self._pages):
            try:
                title = await p.title()
                url = p.url
                cur = " (current)" if p == self._page else ""
                lines.append(f"  {i}: [{title[:60]}]({url[:80]}){cur}")
            except Exception:
                lines.append(f"  {i}: (unavailable)")
        return "\n".join(lines) if lines else "(no tabs)"

    async def switch_tab(self, index: int) -> str:
        if 0 <= index < len(self._pages):
            self._page = self._pages[index]
            try:
                await self._page.bring_to_front()
            except Exception:
                pass
            return "ok"
        return "error: no such tab"

    async def handle_dialog(self, action: str) -> str:
        try:
            dialog = await self._page.wait_for_event("dialog", timeout=5000)
            await (dialog.accept() if action == "accept" else dialog.dismiss())
            return f"Dialog {action}ed"
        except Exception as e:
            return f"error: no dialog: {e}"

    async def file_upload(self, page, selector: str, files: list) -> str:
        await page.locator(selector).set_input_files(files)
        return "ok"

    async def drag(self, page, source: str, target: str) -> str:
        await page.locator(source).drag_to(page.locator(target))
        return "ok"

    async def fill_form(self, page, selector: str, values: dict) -> str:
        for field, value in values.items():
            loc = page.locator(f"{selector} {field}" if field else selector)
            await loc.fill(str(value))
        return "ok"

    async def select_option(self, page, selector: str, values: list) -> str:
        await page.select_option(selector, values)
        return "ok"


class NodriverDriver(BaseDriver):
    """Undetected CDP driver on real Chrome (third fallback)."""

    name = "nodriver"

    def __init__(self, headless: bool = False):
        super().__init__(headless)
        self._browser = None

    async def start(self):
        import nodriver as uc
        self._browser = await uc.start(headless=self.headless)
        tab = await self._browser.get("about:blank")
        self._pages = [tab]
        self._page = tab

    async def stop(self):
        if self._browser is not None:
            try:
                self._browser.stop()
            except Exception:
                pass
        self._browser = None
        self._pages = []
        self._page = None

    async def new_page(self):
        tab = await self._browser.get("about:blank", new_tab=True)
        self._pages.append(tab)
        self._page = tab
        return tab

    async def _close_page_impl(self, page):
        await page.close()

    async def goto(self, tab, url: str, timeout: int = 30000):
        try:
            await tab.get(url)
        except Exception as e:
            return f"error: navigation failed: {e}"
        await asyncio.sleep(1.0)
        return None

    async def evaluate(self, tab, js: str) -> str:
        for _ in range(2):
            try:
                res = await tab.evaluate(js)
            except Exception as e:
                return f"__JS_ERR__: {type(e).__name__}: {str(e)[:150]}"
            if res is None:
                await asyncio.sleep(0.5)
                continue
            if isinstance(res, (dict, list)):
                return json.dumps(res, ensure_ascii=False)[:50000]
            return str(res)[:50000]
        return "null"

    async def title(self, tab) -> str:
        try:
            return str(await tab.evaluate("document.title") or "")
        except Exception:
            return ""

    async def url(self, tab) -> str:
        try:
            return str(await tab.evaluate("location.href") or "")
        except Exception:
            return ""

    async def _resolve_element(self, tab, target: str):
        role, name = _ref_to_role_locator(target)
        if role and name:
            el = await self._find_by_role(tab, role, name)
            if el is not None:
                return el
        if target.startswith(("link ", "button ", "textbox ", "searchbox ", "heading ")):
            role, name = target.split(" ", 1)
            el = await self._find_by_role(tab, role, name)
            if el is not None:
                return el
        try:
            el = await tab.query_selector(target)
            if el is not None:
                return el
        except Exception:
            pass
        try:
            el = await tab.find(target)
            if el is not None:
                return el
        except Exception:
            pass
        return None

    async def _find_by_role(self, tab, role: str, name: str):
        role = role.lower()
        name_l = name.lower()
        try:
            if role in ("textbox", "searchbox", "input", "combobox"):
                els = await tab.query_selector_all("input, textarea, [contenteditable='true']")
                for el in els or []:
                    attrs = dict(el.attrs or {})
                    candidates = [attrs.get("placeholder", ""), attrs.get("aria-label", ""),
                                  attrs.get("name", ""), attrs.get("id", ""), attrs.get("title", "")]
                    if any(name_l in (c or "").lower() for c in candidates):
                        return el
                return None
            return await tab.find(name)
        except Exception:
            return None

    async def click(self, tab, target: str) -> str:
        el = await self._resolve_element(tab, target)
        if el is None:
            return f"error: click failed: element not found: {target[:80]}"
        try:
            await el.click()
            await asyncio.sleep(0.3)
            return "ok"
        except Exception as e:
            return f"error: click failed: {e}"

    async def type_text(self, tab, target: str, text: str) -> str:
        el = await self._resolve_element(tab, target)
        if el is None:
            return f"error: type failed: element not found: {target[:80]}"
        try:
            try:
                await el.clear_input()
            except Exception:
                pass
            await el.send_keys(text)
            return "ok"
        except Exception as e:
            return f"error: type failed: {e}"

    async def hover(self, tab, target: str) -> str:
        el = await self._resolve_element(tab, target)
        if el is None:
            return f"error: hover failed: element not found: {target[:80]}"
        try:
            await el.mouse_move()
            return "ok"
        except Exception as e:
            return f"error: hover failed: {e}"

    async def press_key(self, tab, key: str) -> str:
        key = key.strip()
        js = {
            "Enter": "(()=>{const el=document.activeElement; if(!el) return false; "
                     "if(el.form){el.form.requestSubmit(); return true;} "
                     "el.dispatchEvent(new KeyboardEvent('keydown',{key:'Enter',code:'Enter',keyCode:13,which:13,bubbles:true})); "
                     "return true;})()",
            "Escape": "(()=>{const el=document.activeElement; if(el) el.blur(); "
                      "document.dispatchEvent(new KeyboardEvent('keydown',{key:'Escape',code:'Escape',keyCode:27,bubbles:true})); "
                      "return true;})()",
        }.get(key)
        if js:
            try:
                await tab.evaluate(js)
            except Exception:
                pass
            await asyncio.sleep(0.3)
            return f"Pressed {key}"
        try:
            await tab.evaluate(
                f"(()=>{{const el=document.activeElement; if(el){{"
                f"el.dispatchEvent(new KeyboardEvent('keydown',{{key:'{key}',bubbles:true}})); "
                f"el.dispatchEvent(new KeyboardEvent('keyup',{{key:'{key}',bubbles:true}}));}} return true;}})()")
        except Exception:
            pass
        await asyncio.sleep(0.2)
        return f"Pressed {key}"

    async def screenshot(self, tab, path: str) -> str:
        try:
            await tab.save_screenshot(filename=path, format="png")
            return f"Screenshot saved to {path}"
        except Exception as e:
            return f"error: screenshot failed: {e}"

    async def resize(self, tab, w: int, h: int) -> str:
        try:
            await tab.set_window_size(w, h)
            return f"ok (resized to {w}x{h})"
        except Exception as e:
            return f"error: resize failed: {e}"

    async def back(self, tab) -> str:
        await tab.back()
        return "ok"

    async def tabs(self) -> str:
        lines = []
        for i, tab in enumerate(self._pages):
            try:
                title = str(await tab.evaluate("document.title") or "")
                url = str(await tab.evaluate("location.href") or "")
                cur = " (current)" if tab == self._page else ""
                lines.append(f"  {i}: [{title[:60]}]({url[:80]}){cur}")
            except Exception:
                lines.append(f"  {i}: (unavailable)")
        return "\n".join(lines) if lines else "(no tabs)"

    async def switch_tab(self, index: int) -> str:
        if 0 <= index < len(self._pages):
            self._page = self._pages[index]
            try:
                await self._page.activate()
            except Exception:
                pass
            return "ok"
        return "error: no such tab"

    async def handle_dialog(self, action: str) -> str:
        return "error: dialogs not supported by nodriver backend"

    async def file_upload(self, tab, selector: str, files: list) -> str:
        el = await tab.query_selector(selector)
        if el is None:
            return f"error: file_upload: element not found: {selector}"
        await el.send_file(files)
        return "ok"

    async def drag(self, tab, source: str, target: str) -> str:
        src = await tab.query_selector(source)
        if src is None:
            return f"error: drag failed: source not found: {source}"
        rect = await tab.evaluate(
            f"(()=>{{const el=document.querySelector({json.dumps(target)}); "
            f"if(!el) return null; const r=el.getBoundingClientRect(); "
            f"return [r.x+r.width/2, r.y+r.height/2];}})()")
        if not rect:
            return f"error: drag failed: target not found: {target}"
        await src.mouse_drag(*rect)
        return "ok"

    async def fill_form(self, tab, selector: str, values: dict) -> str:
        for field, value in values.items():
            sel = f"{selector} {field}" if field else selector
            ok = await tab.evaluate(
                f"(()=>{{const el=document.querySelector({json.dumps(sel)}); if(!el) return false; "
                f"const s=Object.getOwnPropertyDescriptor(window.HTMLInputElement.prototype,'value'); "
                f"if(s&&s.set) s.set.call(el, {json.dumps(str(value))}); else el.value={json.dumps(str(value))}; "
                f"el.dispatchEvent(new Event('input',{{bubbles:true}})); "
                f"el.dispatchEvent(new Event('change',{{bubbles:true}})); return true;}})()")
            if not ok:
                return f"error: fill_form: element not found: {sel}"
        return "ok"

    async def select_option(self, tab, selector: str, values: list) -> str:
        for v in values:
            ok = await tab.evaluate(
                f"(()=>{{const el=document.querySelector({json.dumps(selector)}); if(!el) return false; "
                f"el.value={json.dumps(v)}; "
                f"el.dispatchEvent(new Event('change',{{bubbles:true}})); return true;}})()")
            if not ok:
                return f"error: select_option: element not found: {selector}"
        return "ok"


_driver: BaseDriver | None = None


async def _snapshot_text(driver: BaseDriver, page=None) -> str:
    page = page or await driver.current_page()
    url = await driver.url(page)
    title = await driver.title(page)
    header = f"### Page\n- Page URL: {url}\n- Page Title: {title}\n"

    raw = await driver.evaluate(page, _DOM_SCAN_SCRIPT)
    elements = []
    if raw and not raw.startswith("__JS_ERR__"):
        try:
            elements = json.loads(raw)
        except Exception:
            elements = []
    if not elements:
        body = await driver.evaluate(page, "document.body?.innerText || ''")
        if body and not body.startswith("__JS_ERR__"):
            return header + (body[:2000] if body else "")
        return header + "(empty page)"

    lines = []
    for el in elements[:200]:
        role = el.get("role", "")
        name = el.get("name", "")[:80]
        if not name:
            continue
        ref = f"e{abs(hash(str(el))) % 1000000}"
        _element_cache[ref] = el
        display_role = role
        if role in ("a", "link"):
            display_role = "link"
        elif role in ("button", "btn"):
            display_role = "button"
        elif role in ("textbox", "searchbox", "input"):
            display_role = "textbox" if el.get("type") != "search" else "searchbox"
        if display_role in ("button", "link", "textbox", "searchbox", "heading",
                            "combobox", "listbox", "option", "checkbox", "radio",
                            "tab", "menu", "menuitem"):
            lines.append(f'  - [{ref}] {display_role} "{name}"')
        else:
            lines.append(f'  - {display_role} "{name}"')
    return header + ("\n".join(lines) if lines else "(no interactive elements)")


async def _dispatch(name: str, args: dict) -> str:
    global _driver
    page = await _driver.current_page()
    try:
        if name == "browser_navigate":
            res = await _driver.goto(page, args.get("url", ""))
            if res:
                return res
            return await _snapshot_text(_driver, page)
        elif name == "browser_snapshot":
            return await _snapshot_text(_driver, page)
        elif name == "browser_click":
            return await _driver.click(page, args.get("target", ""))
        elif name == "browser_type":
            return await _driver.type_text(page, args.get("target", ""), args.get("text", ""))
        elif name == "browser_press_key":
            return await _driver.press_key(page, args.get("key", ""))
        elif name == "browser_wait_for":
            ms = max(100, min(int(args.get("ms", 1000)), 30000))
            await asyncio.sleep(ms / 1000)
            return f"Waited for {ms//1000}"
        elif name in ("browser_evaluate", "browser_run_code_unsafe"):
            return await _driver.evaluate(page, args.get("function") or args.get("code", ""))
        elif name == "browser_take_screenshot":
            path = tempfile.mktemp(suffix=".png")
            return await _driver.screenshot(page, path)
        elif name == "browser_close":
            return await _driver.close_page(page)
        elif name == "browser_resize":
            return await _driver.resize(page, int(args.get("width", 1920)), int(args.get("height", 1080)))
        elif name == "browser_console_messages":
            msgs = list(_driver._console_logs)
            return "\n".join(msgs[-50:]) if msgs else "(no console messages)"
        elif name == "browser_handle_dialog":
            return await _driver.handle_dialog(args.get("action", "accept"))
        elif name == "browser_file_upload":
            return await _driver.file_upload(page, args.get("selector", ""), args.get("files", []))
        elif name in ("browser_drag", "browser_drop"):
            return await _driver.drag(page, args.get("source", ""), args.get("target", ""))
        elif name == "browser_fill_form":
            try:
                values = json.loads(args.get("values", "{}"))
            except Exception:
                values = {}
            return await _driver.fill_form(page, args.get("selector", ""), values)
        elif name == "browser_navigate_back":
            return await _driver.back(page)
        elif name == "browser_network_requests":
            reqs = _driver._network_requests[-100:]
            return json.dumps(reqs, ensure_ascii=False) if reqs else "(no requests)"
        elif name == "browser_network_request":
            url = args.get("url", "")
            for r in _driver._network_requests:
                if url in r.get("url", ""):
                    return json.dumps(r, ensure_ascii=False)
            return f"error: request not found: {url}"
        elif name == "browser_tabs":
            index = args.get("index")
            if index is not None:
                return await _driver.switch_tab(int(index))
            return await _driver.tabs()
        elif name == "browser_hover":
            return await _driver.hover(page, args.get("target", ""))
        elif name == "browser_select_option":
            try:
                values = json.loads(args.get("values", "[]"))
            except Exception:
                values = []
            return await _driver.select_option(page, args.get("selector", ""), values)
        return f"error: unknown tool {name}"
    except Exception as e:
        logger.exception("Tool %s failed", name)
        return f"error: {e}"


async def _serve() -> None:
    try:
        async with stdio_server() as (read_stream, write_stream):
            await server.run(read_stream, write_stream, server.create_initialization_options())
    finally:
        try:
            await _driver.stop()
        except Exception:
            pass


def main() -> None:
    parser = argparse.ArgumentParser(description="Anti-detect browser MCP server")
    parser.add_argument("--backend", choices=("camoufox", "nodriver"), default="camoufox")
    parser.add_argument("--headless", action="store_true", default=False)
    args = parser.parse_args()

    global _driver
    if args.backend == "camoufox":
        _driver = CamoufoxDriver(headless=args.headless)
    else:
        _driver = NodriverDriver(headless=args.headless)
    asyncio.run(_serve())


if __name__ == "__main__":
    main()
