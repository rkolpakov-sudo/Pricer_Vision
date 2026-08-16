import asyncio
import json
import logging
from mcp.server.lowlevel import Server
from mcp.server.stdio import stdio_server
from mcp import types
from DrissionPage import ChromiumPage

logging.basicConfig(level=logging.INFO, format="%(name)s [%(levelname)s] %(message)s")
logger = logging.getLogger("pricer-mcp")

_page = None

TAG_MAP = {
    "a": "lnk", "button": "btn", "input": "inp",
    "select": "sel", "textarea": "tbox", "label": "lbl",
    "h1": "h1", "h2": "h2", "h3": "h3", "h4": "h4",
    "p": "txt", "li": "opt", "td": "dat", "th": "hdr",
    "nav": "nav", "header": "hdr", "footer": "ftr",
    "main": "main", "aside": "side", "dialog": "dlg",
    "div": "div", "span": "span", "img": "img",
}

REGION_TAGS = {"header": "hdr", "nav": "nav", "main": "main",
               "aside": "side", "footer": "ftr", "dialog": "dlg"}

SELECTOR = "h1,h2,h3,h4,a,button,input,select,textarea,label,div,span,p,li,td,th,nav,header,footer,main,aside,dialog,[role]"

DOM_SCRIPT = """
const tags = '""" + SELECTOR + """';
const all = document.querySelectorAll(tags);
const out = [];
let idx = 0;
const seen = new Set();
const regionTags = {header:'hdr',nav:'nav',main:'main',aside:'side',footer:'ftr',dialog:'dlg'};
const tagMap = {a:'lnk',button:'btn',input:'inp',select:'sel',textarea:'tbox',label:'lbl',
                h1:'h1',h2:'h2',h3:'h3',h4:'h4',p:'txt',li:'opt',td:'dat',th:'hdr',
                nav:'nav',header:'hdr',footer:'ftr',main:'main',aside:'side',dialog:'dlg',
                div:'div',span:'span',img:'img'};
for (const el of all) {
    if (idx >= 500) break;
    const tag = el.tagName.toLowerCase();
    if (tag === 'input' && el.type === 'hidden') continue;
    const rect = el.getBoundingClientRect();
    if (rect.width < 1 || rect.height < 1) continue;
    const uid = tag + '_' + Math.round(rect.left) + '_' + Math.round(rect.top);
    if (seen.has(uid)) continue;
    seen.add(uid);
    const role = el.getAttribute('role') || '';
    const ariaLabel = el.getAttribute('aria-label') || '';
    const ph = el.getAttribute('placeholder') || '';
    const title = el.getAttribute('title') || '';
    const alt = el.getAttribute('alt') || '';
    const txt = (el.textContent || '').trim().slice(0, 120);
    const href = el.getAttribute('href') || '';
    const value = el.getAttribute('value') || '';
    const cls = el.className || '';
    const forAttr = el.getAttribute('for') || '';
    let kind = tagMap[tag] || tag;
    if (tag === 'input') {
        const t = el.type || 'text';
        if (t === 'text' || t === 'search' || t === 'email' || t === 'tel') kind = 'txt';
        else if (t === 'submit' || t === 'button') kind = 'btn';
        else if (t === 'number') kind = 'num';
        else if (t === 'checkbox') kind = 'chk';
        else if (t === 'radio') kind = 'rad';
        else if (t === 'password') kind = 'pwd';
        else kind = t;
    }
    if (role) kind = role;
    if (tag === 'a' && !href) kind = 'anc';
    let label = ariaLabel || ph || title || alt || value || '';
    if (!label && forAttr && el.labels && el.labels.length) {
        label = el.labels[0].textContent.trim().slice(0, 100);
    }
    if (!label && tag === 'a' && href) label = href.slice(0, 80);
    if (!label) label = txt;
    if (!label && cls) label = cls.slice(0, 60);
    if (!label) label = kind;
    let region = '';
    let p = el.parentElement;
    for (let i = 0; i < 5 && p; i++) {
        const pt = p.tagName.toLowerCase();
        if (regionTags[pt]) { region = regionTags[pt]; break; }
        const pr = p.getAttribute('role') || '';
        if (pr === 'banner') { region = 'hdr'; break; }
        if (pr === 'navigation') { region = 'nav'; break; }
        if (pr === 'main') { region = 'main'; break; }
        if (pr === 'complementary') { region = 'side'; break; }
        if (pr === 'contentinfo') { region = 'ftr'; break; }
        if (pr === 'dialog' || pr === 'alertdialog') { region = 'dlg'; break; }
        p = p.parentElement;
    }
    let state = '';
    if (el.disabled) state += 'd';
    if (el.readOnly) state += 'ro';
    if (el.required) state += 'req';
    if (el.checked) state += 'c';
    if (el.selected || el.getAttribute('aria-selected') === 'true') state += 's';
    const exp = el.getAttribute('aria-expanded');
    if (exp === 'true') state += 'e';
    else if (exp === 'false') state += 'ec';
    out.push({i: idx, t: kind, l: label.slice(0, 100),
               r: region, s: state, u: href.slice(0, 120)});
    idx++;
}
return JSON.stringify(out);
"""


async def _sync(fn, *args):
    return await asyncio.to_thread(fn, *args)


def _ensure_page():
    global _page
    if _page is not None:
        try:
            _ = _page.url
            return
        except Exception:
            logger.warning("Page dead, recreating")
            _page = None
    _page = ChromiumPage()
    _page.get('about:blank')
    logger.info("Browser ready via DrissionPage")


def _run_js(js: str):
    _ensure_page()
    return _page.run_js(js, as_expr=False)


def _dom_summary() -> str:
    _ensure_page()
    try:
        raw = _run_js(DOM_SCRIPT)
        items = json.loads(raw) if isinstance(raw, str) else []
        if not items:
            url = _page.url
            title = _page.title
            logger.info("DOM empty on URL: %s title: %s", url[:100], title[:80])
            html_len = len(_page.html) if _page.html else 0
            return f"(no interactive elements — {html_len} bytes HTML)"
        parts = [f"{item['i']}:{item['t']}" + (f" \"{item['l']}\"" if item['l'] else "") +
                 (f" [{item['r']}]" if item.get('r') else "") +
                 (f" ({item['s']})" if item.get('s') else "")
                 for item in items]
        return "\n".join("  " + p for p in parts)
    except Exception as e:
        logger.warning(f"DOM error: {e}")
        return f"(dom error: {e})"


def _find_element(ref: int):
    _ensure_page()
    js = f"""
const tags = '{SELECTOR}';
const all = document.querySelectorAll(tags);
const vis = [];
for (const el of all) {{
    const tag = el.tagName.toLowerCase();
    if (tag === 'input' && el.type === 'hidden') continue;
    const r = el.getBoundingClientRect();
    if (r.width < 1 || r.height < 1) continue;
    vis.push(el);
}}
const el = vis[{ref}];
if (!el) return null;
el.setAttribute('data-pricer-ref', '1');
return 'ok';
"""
    result = _page.run_js(js, as_expr=False)
    if result != 'ok':
        return None
    el = _page.ele('@data-pricer-ref')
    _clean_ref()
    return el


def _clean_ref():
    try:
        _page.run_js("document.querySelector('[data-pricer-ref]')?.removeAttribute('data-pricer-ref')", as_expr=False)
    except Exception:
        pass


TOOLS = [
    types.Tool(name="navigate", description="Navigate to URL. Returns compact DOM of interactive elements.",
               inputSchema={"type": "object", "properties": {"url": {"type": "string"}}, "required": ["url"]}),
    types.Tool(name="snapshot", description="DOM snapshot with ref numbers, compressed types (btn,txt,lnk...), regions [hdr,nav,main,side,ftr,dlg], state (d=disabled,ro=readonly,c=checked).",
               inputSchema={"type": "object", "properties": {}}),
    types.Tool(name="click", description="Click element by ref number.",
               inputSchema={"type": "object", "properties": {"ref": {"type": "integer"}}, "required": ["ref"]}),
    types.Tool(name="type_text", description="Type text into input field by ref number.",
               inputSchema={"type": "object", "properties": {"ref": {"type": "integer"}, "text": {"type": "string"}}, "required": ["ref", "text"]}),
    types.Tool(name="press_key", description="Press keyboard key: Enter, Escape, ArrowDown, ArrowUp, Tab, Backspace.",
               inputSchema={"type": "object", "properties": {"key": {"type": "string"}}, "required": ["key"]}),
    types.Tool(name="wait", description="Wait ms (100-15000). Returns DOM summary.",
               inputSchema={"type": "object", "properties": {"ms": {"type": "integer"}}, "required": ["ms"]}),
    types.Tool(name="query_dom", description="Search elements by CSS selector.",
               inputSchema={"type": "object", "properties": {"expression": {"type": "string"}}, "required": ["expression"]}),
    types.Tool(name="extract_text", description="Extract visible text. No selector = entire page.",
               inputSchema={"type": "object", "properties": {"selector": {"type": "string"}}}),
    types.Tool(name="get_page_info", description="Current page URL and title.",
               inputSchema={"type": "object", "properties": {}}),
]


async def handle_call_tool(name: str, arguments: dict) -> types.CallToolResult:
    try:
        if name == "navigate":
            url = str(arguments.get("url", ""))
            await _sync(_ensure_page)
            try:
                await _sync(lambda: _page.get(url))
            except Exception as e:
                return types.CallToolResult(content=[types.TextContent(type="text", text=f"error: navigate failed: {e}")], isError=True)
            result = await _sync(_dom_summary)
            return types.CallToolResult(content=[types.TextContent(type="text", text=result)])

        elif name == "snapshot":
            result = await _sync(_dom_summary)
            return types.CallToolResult(content=[types.TextContent(type="text", text=result)])

        elif name == "click":
            ref = int(arguments.get("ref", -1))
            el = await _sync(_find_element, ref)
            if el is None:
                return types.CallToolResult(content=[types.TextContent(type="text", text=f"error: element {ref} not found")], isError=True)
            try:
                await _sync(el.click)
            except Exception as e:
                return types.CallToolResult(content=[types.TextContent(type="text", text=f"error: click failed: {e}")], isError=True)
            return types.CallToolResult(content=[types.TextContent(type="text", text="ok")])

        elif name == "type_text":
            ref = int(arguments.get("ref", -1))
            text = str(arguments.get("text", ""))
            el = await _sync(_find_element, ref)
            if el is None:
                return types.CallToolResult(content=[types.TextContent(type="text", text=f"error: element {ref} not found")], isError=True)
            try:
                await _sync(el.input, text)
            except Exception as e:
                return types.CallToolResult(content=[types.TextContent(type="text", text=f"error: type_text failed: {e}")], isError=True)
            return types.CallToolResult(content=[types.TextContent(type="text", text="ok")])

        elif name == "press_key":
            key = str(arguments.get("key", ""))
            await _sync(_ensure_page)
            try:
                await _sync(lambda: _page.run_js(f"document.dispatchEvent(new KeyboardEvent('keydown', {{key: '{key}'}}))", as_expr=False))
            except Exception as e:
                return types.CallToolResult(content=[types.TextContent(type="text", text=f"error: press_key failed: {e}")], isError=True)
            return types.CallToolResult(content=[types.TextContent(type="text", text="ok")])

        elif name == "wait":
            ms = min(max(int(arguments.get("ms", 1000)), 100), 15000)
            await asyncio.sleep(ms / 1000)
            result = await _sync(_dom_summary)
            return types.CallToolResult(content=[types.TextContent(type="text", text=result)])

        elif name == "query_dom":
            expr = str(arguments.get("expression", ""))
            await _sync(_ensure_page)
            js = f"""
const results = [];
try {{
    const els = document.querySelectorAll('{expr}');
    for (const el of els) {{
        const r = el.getBoundingClientRect();
        if (r.width === 0 || r.height === 0) continue;
        results.push({{tag: el.tagName.toLowerCase(), text: (el.textContent || '').trim().slice(0, 120)}});
    }}
}} catch(e) {{}}
return JSON.stringify(results.slice(0, 30));
"""
            raw = await _sync(lambda: _page.run_js(js, as_expr=False))
            items = json.loads(raw) if isinstance(raw, str) else []
            if not items:
                return types.CallToolResult(content=[types.TextContent(type="text", text=f"No matches for: {expr}")])
            lines = [f"Found {len(items)} for '{expr}':"]
            for r in items:
                lines.append(f"  <{r['tag']}> {r['text']}")
            return types.CallToolResult(content=[types.TextContent(type="text", text="\n".join(lines))])

        elif name == "extract_text":
            selector = str(arguments.get("selector", ""))
            await _sync(_ensure_page)
            js = """
const s = '""" + selector.replace("'", "\\'") + """';
if (!s) { const t = document.body?.innerText?.slice(0, 5000) || '(empty)'; return t; }
try {
    const el = document.querySelector(s);
    return el ? (el.innerText || el.textContent || '').slice(0, 3000) : 'error: selector not found';
} catch(e) { return 'error: ' + e.message; }
"""
            raw = await _sync(lambda: _page.run_js(js, as_expr=False))
            return types.CallToolResult(content=[types.TextContent(type="text", text=str(raw) if raw else "(empty)")])

        elif name == "get_page_info":
            await _sync(_ensure_page)
            return types.CallToolResult(content=[types.TextContent(type="text", text=f"URL: {_page.url}\nTitle: {_page.title}")])

        else:
            return types.CallToolResult(content=[types.TextContent(type="text", text=f"error: unknown tool '{name}'")], isError=True)

    except Exception as e:
        logger.exception(f"Tool {name} failed")
        return types.CallToolResult(content=[types.TextContent(type="text", text=f"error: {e}")], isError=True)


async def main():
    server = Server("pricer-browser")

    @server.list_tools()
    async def list_tools():
        return TOOLS

    @server.call_tool()
    async def call_tool(name: str, arguments: dict):
        return await handle_call_tool(name, arguments or {})

    async with stdio_server() as (read_stream, write_stream):
        await server.run(read_stream, write_stream, server.create_initialization_options())


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except Exception as e:
        logger.exception("Fatal startup error")
        import sys
        sys.stderr.write(f"FATAL: {e}\n")
        sys.stderr.flush()
        raise
