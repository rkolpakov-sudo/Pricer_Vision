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
import hashlib
import json
import logging
import re
import tempfile

from mcp.server.lowlevel import Server
from mcp.server.stdio import stdio_server
from mcp import types

logging.basicConfig(level=logging.INFO, format="%(name)s %(message)s")
logger = logging.getLogger("pricer.browser")


async def _handle_list_tools(ctx, params) -> types.ListToolsResult:
    return types.ListToolsResult(tools=TOOL_DEFS)


async def _dispatch_with_recovery(name: str, args: dict) -> str:
    """Выполняет tool-call; при «browser/context has been closed» — перезапускает
    браузер и повторяет ОДИН раз.

    Без этого падение браузера каскадно ломало весь прогон: «BrowserContext.new_page:
    Target page, context or browser has been closed» накапливал ошибки MCP → circuit
    breaker OPEN → все строки убивались (MCP=0 / MCP circuit open).
    """
    if _driver._browser is None:
        await _driver.start()
    result = await _dispatch(name, args)
    if "has been closed" in result:
        logger.warning("Browser closed — перезапуск браузера и повтор попытки (%s)", name)
        try:
            await _driver.stop()
        except Exception:
            pass
        await _driver.start()
        result = _dispatch(name, args)
    return result


async def _handle_call_tool(ctx, params) -> types.CallToolResult:
    result = await _dispatch_with_recovery(params.name, params.arguments or {})
    return types.CallToolResult(content=[types.TextContent(type="text", text=result)])


async def _handle_call_tool_v1(name: str, arguments: dict) -> list[types.TextContent]:
    result = await _dispatch_with_recovery(name, arguments or {})
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

_EXTRACT_SCRIPT = """
(() => {
  const out = { url: location.href, price: null, name: null, article: null, availability: null };
  try {
    const scripts = document.querySelectorAll('script[type="application/ld+json"]');
    for (const s of scripts) {
      try {
        const data = JSON.parse(s.textContent);
        const items = Array.isArray(data) ? data : [data];
        for (const it of items) {
          const prod = it['@type'] === 'Product' ? it : (it.mainEntity && it.mainEntity['@type'] === 'Product' ? it.mainEntity : null);
          if (!prod) continue;
          if (!out.price && prod.offers) {
            const offers = Array.isArray(prod.offers) ? prod.offers : [prod.offers];
            for (const o of offers) {
              if (o && o.price && !out.price) { out.price = String(o.price); }
              if (o && o.availability && !out.availability) { out.availability = String(o.availability); }
            }
          }
          if (!out.name && prod.name) out.name = String(prod.name);
          if (!out.article && prod.sku) out.article = String(prod.sku);
        }
      } catch (e) {}
    }
    if (!out.price) {
      const m = document.querySelector('[itemprop="price"]');
      if (m) out.price = m.getAttribute('content') || m.textContent.trim();
    }
    if (!out.price) {
      const dp = document.querySelector('[data-price]');
      if (dp) out.price = dp.getAttribute('data-price') || dp.textContent.trim();
    }
    if (!out.price) {
      const el = document.querySelector('[class*="price"] [class*="current"], .product-price, [class*="price"]');
      if (el) out.price = el.textContent.trim();
    }
    if (!out.name) {
      const og = document.querySelector('meta[property="og:title"]');
      if (og) out.name = og.getAttribute('content');
    }
    if (!out.name) {
      const h1 = document.querySelector('h1');
      if (h1) out.name = h1.textContent.trim();
    }
    if (!out.name) out.name = document.title;
    if (!out.article) {
      const sku = document.querySelector('[itemprop="sku"], [data-sku], [class*="article"]');
      if (sku) out.article = sku.textContent.trim();
    }
  } catch (e) { out.error = String(e); }
  return JSON.stringify(out);
})();
"""

TOOL_DEFS = [
    types.Tool(name="browser_navigate", description="Navigate to URL. Returns accessibility tree.",
               inputSchema={"type": "object", "properties": {"url": {"type": "string"}}, "required": ["url"]}),
    types.Tool(name="browser_snapshot", description="Get accessibility tree snapshot of current page.",
               inputSchema={"type": "object", "properties": {}}),
    types.Tool(name="browser_extract", description="Structured extraction of current page: JSON {url, price, name, article, availability}. Uses JSON-LD, microdata, data-price, DOM classes.",
               inputSchema={"type": "object", "properties": {}}),
    types.Tool(name="browser_click", description="Click element by CSS selector, Playwright role locator, or snapshot ref.",
               inputSchema={"type": "object", "properties": {"target": {"type": "string"}}, "required": ["target"]}),
    types.Tool(name="browser_type", description="Type text into input field.",
               inputSchema={"type": "object", "properties": {"target": {"type": "string"}, "text": {"type": "string"}}, "required": ["target", "text"]}),
    types.Tool(name="browser_press_key", description="Press keyboard key: Enter, Escape, ArrowDown, ArrowUp, Tab, Backspace.",
               inputSchema={"type": "object", "properties": {"key": {"type": "string"}}, "required": ["key"]}),
    types.Tool(name="browser_wait_for", description="Wait for ms milliseconds.",
               inputSchema={"type": "object", "properties": {"ms": {"type": "integer", "default": 1000}}}),
    types.Tool(name="browser_evaluate", description="Execute JavaScript in page context. Code MUST be an arrow function: () => expr or async () => expr. Do NOT use return statement. Example: () => document.title",
               inputSchema={"type": "object", "properties": {"function": {"type": "string"}}, "required": ["function"]}),
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

# window.set*Спуферы, которые Camoufox оставляет перечислимыми на window
# (известная утечка automatization-сигнала, github.com/daijro/camoufox#723:
# в стоковом Firefox на window один такой ключ — setResizable). Сайт может
# поймать автоматизацию одной строкой Object.keys(window).filter(k=>k.startsWith('set')).
# Удаляем их после вызова спуфера на каждой навигации (C++-слой держит значения сам).
_CAMOUFOX_SETTERS = [
    "setFontSpacingSeed", "setAudioFingerprintSeed", "setCanvasSeed",
    "setNavigatorPlatform", "setNavigatorOscpu", "setNavigatorUserAgent",
    "setNavigatorHardwareConcurrency", "setWebGLVendor", "setWebGLRenderer",
    "setScreenDimensions", "setScreenColorDepth", "setTimezone",
    "setWebRTCIPv4", "setFontList", "setSpeechVoices",
]


def _setter_cleanup_script() -> str:
    """JS init-скрипт: удаляет window.set*Спуферы после спуфинга.

    Регистрируется ПОСЛЕ init-скрипта Camoufox (init-скрипты выполняются в порядке
    регистрации) → на каждой навигации сначала применяется спуфинг, затем сеттеры
    убираются из перечисления, значение остаётся в C++-слое.
    """
    names = ", ".join(json.dumps(n) for n in _CAMOUFOX_SETTERS)
    return (
        "(() => { const _cf = [" + names + "]; "
        "for (const n of _cf) { try { delete window[n]; } catch (e) {} } })()"
    )


def _pinned_windows_preset() -> dict | None:
    """Стабильный реальный Windows-пресет отпечатка (одинаковый на каждом запуске).

    Для постоянного профиля нужен ОДИН И ТОТ ЖЕ отпечаток между сессиями, иначе
    «возвращающийся пользователь = другой компьютер» (issues #723). Берём конкретный
    пресет из встроенного бандла v150 (реальные отпечатки) по детерминированному
    индексу. UA актуализируется под текущую версию Firefox самим Camoufox.
    """
    try:
        from camoufox.fingerprints import load_presets
        presets = ((load_presets(ff_version=152) or {}).get("presets", {})).get("windows", [])
    except Exception:
        presets = []
    if not presets:
        return None
    idx = int(hashlib.md5(b"pricer-camoufox-windows").hexdigest(), 16) % len(presets)
    return presets[idx]


def _camoufox_launch_kwargs(headless: bool, *, locale: str = "ru-RU",
                            timezone: str = "Europe/Moscow", geoip: bool = False,
                            humanize: bool = True, persistent_profile: bool = False,
                            profile_dir: str = "data/camoufox_profile",
                            pinned_fingerprint: bool = False) -> dict:
    """Параметры запуска AsyncCamoufox с консистентным отпечатком.

    Закрывает несоответствие «RU-сайт + RU-IP + en-US locale» (триггер ServicePipe):
    locale задаёт Accept-Language/navigator.language, timezone — таймзону.
    geoip=True считает locale/timezone/координаты по локальному IP и подставляет
    webrtc:ipv4 (требует camoufox[geoip]).

    persistent_profile + pinned_fingerprint — «возвращающийся пользователь»:
    профиль (куки/localStorage) хранится между сессиями, а отпечаток закреплён
    за одним реальным Windows-пресетом (иначе неконсистентность issues #723).
    """
    kwargs: dict = {
        "headless": headless,
        "os": "windows",
        "fingerprint_preset": True,
        "humanize": humanize,
    }
    if pinned_fingerprint:
        preset = _pinned_windows_preset()
        if preset is not None:
            kwargs["fingerprint_preset"] = preset
    if persistent_profile:
        kwargs["persistent_context"] = True
        kwargs["user_data_dir"] = profile_dir
        kwargs["enable_cache"] = True
        # Постоянный профиль + session-restore Firefox = «два браузера»: после
        # нечистого закрытия (force-kill/таймаут/рестарт bridge) Firefox при
        # следующем запуске восстанавливает прошлые окна ПОВЕРХ новых. Отключаем
        # восстановление сессии, чтобы при старте открывалась только наша страница.
        kwargs["firefox_user_prefs"] = {
            "browser.sessionstore.resume_from_crash": False,
            "browser.sessionstore.max_resumed_crashes": 0,
            "browser.sessionstore.restore_on_demand": False,
            "browser.sessionstore.restore_tabs_lazily": False,
            "browser.startup.page": 0,
            "toolkit.startup.max_resumed_crashes": -1,
        }
    if geoip:
        kwargs["geoip"] = True
    else:
        kwargs["locale"] = locale
        kwargs["config"] = {"timezone": timezone}
    return kwargs


def _ref_to_role_locator(ref: str):
    node = _element_cache.get(ref)
    if node and isinstance(node, dict) and node.get("role") and node.get("name"):
        return node["role"], node["name"]
    return None, None


_SNAPSHOT_REF_RE = re.compile(r"^e\d+$")

# Заполняет первый ВИДИМЫЙ поисковый input на странице (тип search / name*=search /
# placeholder с «Поиск»/«Искать»). Возвращает описание найденного поля или null.
_SEARCH_INPUT_FILL_JS = r"""
(text) => {
  const SELECTORS = [
    'input[type="search"]',
    'input[name*="search" i]',
    'input[name="q"]',
    'input[name="text"]',
    'input[placeholder*="Поиск" i]',
    'input[placeholder*="Искать" i]',
    'input[placeholder*="Оригинальные" i]',
    'input[aria-label*="Поиск" i]',
  ];
  const seen = new Set();
  for (const sel of SELECTORS) {
    for (const inp of document.querySelectorAll(sel)) {
      const key = inp.name + '|' + inp.type + '|' + (inp.placeholder || '');
      if (seen.has(key)) continue;
      seen.add(key);
      if (inp.offsetParent === null && !inp.matches(':focus')) continue;
      const setter = Object.getOwnPropertyDescriptor(window.HTMLInputElement.prototype, 'value').set;
      setter.call(inp, text);
      inp.dispatchEvent(new Event('input', {bubbles: true}));
      inp.dispatchEvent(new Event('change', {bubbles: true}));
      const desc = [inp.type, inp.name, inp.placeholder].filter(Boolean).join(' ');
      return desc || ('input ' + sel);
    }
  }
  return null;
}
"""
# Last-resort: берём первый видимый <input> на странице (кроме hidden/submit/button/reset).
_SEARCH_INPUT_FALLBACK_JS = r"""
() => {
  for (const inp of document.querySelectorAll('input')) {
    if (['hidden','submit','button','reset','file','image'].includes(inp.type)) continue;
    if (inp.offsetParent === null && !inp.matches(':focus')) continue;
    const setter = Object.getOwnPropertyDescriptor(window.HTMLInputElement.prototype, 'value').set;
    setter.call(inp, '');
    inp.dispatchEvent(new Event('input', {bubbles: true}));
    const desc = [inp.type, inp.name, inp.placeholder].filter(Boolean).join(' ');
    return desc || ('input[' + (inp.type || 'text') + ']');
  }
  return null;
}
"""

# Поиск кликабельного элемента в ЖИВОМ DOM по (роль, имя) или CSS-селектору.
# Используется двумя путями:
#   1) Быстрый отказ — до 10с таймаута get_by_role/CSS-клика проверяем, существует
#      ли элемент вообще (SPA перерисовалась, ref устарел). Нет элемента → мгновенная
#      ошибка вместо 10с ожидания.
#   2) Fallback клика — когда честный Playwright-клик не смог (actionability/имя не
#      сошлось), ищем элемент сами и кликаем через JS без расхода LLM-раунда.
_CLICK_FINDER_JS = r"""
const _norm = (s) => (s || '').replace(/\s+/g, ' ').trim();
const _firstVisible = (list) => {
  for (const e of list || []) {
    const r = e.getBoundingClientRect();
    if (r.width > 1 && r.height > 1) return e;
  }
  return (list && list[0]) || null;
};
const _roleSel = {
  link: 'a[href]',
  button: 'button, [role="button"], input[type="button"], input[type="submit"], input[type="reset"]',
  textbox: 'input, textarea, [contenteditable="true"]',
  searchbox: 'input[type="search"], input, textarea',
  heading: 'h1,h2,h3,h4,h5,h6,[role="heading"]',
  checkbox: 'input[type="checkbox"], [role="checkbox"]',
  radio: 'input[type="radio"], [role="radio"]',
  combobox: 'select, [role="combobox"]',
  option: 'option, [role="option"]',
  tab: '[role="tab"]',
  menu: '[role="menu"]',
  menuitem: '[role="menuitem"]',
  listbox: '[role="listbox"]',
};
const _TAG_ROLES = new Set(['a','div','span','p','li','td','th','tr','ul','ol','section','article','nav','form','table']);
const _findEl = (arg) => {
  let el = null;
  if (arg.css) {
    try { el = _firstVisible(Array.from(document.querySelectorAll(arg.css))); }
    catch (e) { return null; }
  } else if (arg.role) {
    const sel = _roleSel[arg.role];
    let els = [];
    if (sel) { try { els = Array.from(document.querySelectorAll(sel)); } catch (e) {} }
    if (!els.length) {
      try {
        els = _TAG_ROLES.has(arg.role)
          ? Array.from(document.querySelectorAll(arg.role))
          : Array.from(document.querySelectorAll('[role="' + arg.role + '"]'));
      } catch (e) { els = []; }
    }
    const want = _norm(arg.name || '').toLowerCase();
    if (want) {
      const exact = els.filter(e => _norm(e.textContent).toLowerCase() === want);
      if (exact.length) el = _firstVisible(exact);
      else {
        const has = els.filter(e => e.textContent && _norm(e.textContent).toLowerCase().includes(want));
        if (has.length) el = _firstVisible(has);
      }
    }
    if (!el && els.length) el = _firstVisible(els);
  }
  return el;
};
"""

_CLICK_FIND_JS = "((arg) => {\n" + _CLICK_FINDER_JS + r"""
  const el = _findEl(arg);
  if (!el) return { found: false };
  const anchor = (el.tagName === 'A') ? el : (el.closest ? el.closest('a') : null);
  return {
    found: true,
    tag: el.tagName.toLowerCase(),
    href: anchor && anchor.href ? anchor.href : '',
    text: _norm(el.textContent).slice(0, 80),
  };
})"""

_CLICK_FORCE_JS = "((arg) => {\n" + _CLICK_FINDER_JS + r"""
  const el = _findEl(arg);
  if (!el) return { ok: false, reason: 'not-found' };
  try {
    el.scrollIntoView({ block: 'center' });
    const r = el.getBoundingClientRect();
    const x = r.left + r.width / 2;
    const y = r.top + r.height / 2;
    const base = { bubbles: true, cancelable: true, view: window, clientX: x, clientY: y, button: 0, detail: 1 };
    el.dispatchEvent(new MouseEvent('pointerdown', Object.assign({}, base, { pointerId: 1, pointerType: 'mouse' })));
    el.dispatchEvent(new MouseEvent('mousedown', base));
    el.dispatchEvent(new MouseEvent('pointerup', Object.assign({}, base, { pointerId: 1, pointerType: 'mouse' })));
    el.dispatchEvent(new MouseEvent('mouseup', base));
    el.dispatchEvent(new MouseEvent('click', base));
    return { ok: true };
  } catch (e) {
    return { ok: false, reason: String(e).slice(0, 120) };
  }
})"""


def _is_snapshot_ref(target: str) -> bool:
    """target вида e1234 — ref из accessibility-снапшота (не CSS-селектор)."""
    return bool(target and _SNAPSHOT_REF_RE.match(target.strip()))


def _action_error_hint(exc: Exception) -> str:
    """Понятная агенту причина/подсказка для частых ошибок Playwright.

    Раньше ошибка возвращалась сырой («Timeout 10000ms exceeded») — LLM тратила
    2–3 раунда на слепое восстановление. Теперь сразу объясняем причину и даём
    формат корректного target (CSS/роль).
    """
    msg = str(exc)
    if "strict mode violation" in msg:
        m = re.search(r"resolved to (\d+) elements", msg)
        n = m.group(1) if m else "несколько"
        return (f"strict mode violation: селектор совпадает с {n} элементами. "
                "Уточни селектор (CSS с атрибутом или индексом) либо используй роль "
                "(например 'button \"Найти\"' / 'textbox \"Поиск\"').")
    if "Timeout" in msg or "timeout" in msg:
        return ("элемент не найден/недоступен по указанному target в отведённое время. "
                "Обнови browser_snapshot и используй CSS-селектор. Для поисковой строки "
                "подойдёт 'input[type=\"search\"]' / 'input[name=\"search\"]' — НЕ роль "
                "'textbox', т.к. поисковые поля имеют роль 'searchbox'.")
    return ""


def _resolve_action_target(target: str):
    """Разбирает target на роль-локатор / CSS / мгновенную ошибку.

    Быстрый отказ для устаревших хеш-refов снапшота (e1234), которых нет в
    _element_cache: вместо page.locator('e1234') → 10с таймаут → ошибка + 2–3
    восстановительных LLM-раунда возвращаем мгновенную ошибку с подсказкой.

    ВСЕГДА возвращает 3-кортеж: ('role', role, name) | ('css', selector, '')
    | ('error', message, ''). Единая арность обязательна: вызывающие обращаются
    к kind[2] (регрессия: 2-кортеж ('css', t) давал IndexError «tuple index
    out of range» на каждом CSS-клике/вводе → агент не мог работать).
    """
    t = (target or "").strip()
    if not t:
        return ("error", "error: пустой target — укажи CSS-селектор или роль элемента.", "")
    # LLM иногда оборачивает ref в скобки: [e706] → e706
    t = re.sub(r'^\[(e\d+)\]$', r'\1', t)
    # Модель иногда передаёт целиком get_by_role(...) / locator(...) как target —
    # вытаскиваем роль и имя, чтобы не парсить это как CSS-селектор.
    m = re.search(r'get_by_role\(\s*["\'](\w+)["\']\s*,\s*(?:name\s*=\s*)?["\']([^"\']+)["\']', t)
    if m:
        return ("role", m.group(1), m.group(2))
    if _is_snapshot_ref(t):
        role, name = _ref_to_role_locator(t)
        if role and name:
            return ("role", role, name)
        return ("error",
                "error: target — устаревший ref снапшота (%s), элемент уже не найден "
                "(страница могла перерисоваться). Используй CSS-селектор "
                "(например 'input[name=\"search\"]') или роль (например 'textbox \"Поиск\"'), "
                "либо обнови browser_snapshot." % t, "")
    for prefix in ("link ", "button ", "textbox ", "searchbox ", "heading "):
        if t.startswith(prefix):
            role, name = t.split(" ", 1)
            return ("role", role, name)
    return ("css", t, "")


def _wrap_js_if_needed(js: str) -> str:
    """Автоматически оборачивает JS-код в arrow function если нужно.

    LLM часто передаёт в browser_evaluate裸ный выражение или функцию с return.
    page.evaluate() требует () => expr или function() { return expr; }.
    """
    s = js.strip()
    # Уже arrow function — не трогаем
    if s.startswith("()") or s.startswith("async ()") or s.startswith("(()"):
        return js
    # Уже function() — не трогаем
    if s.startswith("function"):
        return js
    # Содержит return без обёртки — оборачиваем
    if "return " in s:
        return f"() => {{ {s} }}"
    # Простое выражение — оборачиваем в arrow
    if any(s.startswith(k) for k in ("document.", "window.", "navigator.", "JSON.", "Math.",
                                      "String(", "Number(", "Boolean(", "parseInt(",
                                      "parseFloat(", "alert(", "console.")):
        return f"() => {s}"
    return js


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
        self._hide_setters = True

    async def start(self):
        from camoufox.async_api import AsyncCamoufox
        # Защита от двойного запуска: если браузер уже поднят (повторный вызов
        # start() при живом _browser) — не плодим второй экземпляр.
        if self._browser is not None:
            logger.info("camoufox уже запущен — повторный start() проигнорирован")
            return
        # Антибот-настройки из config/settings.yaml (Qt-free). При недоступности
        # config_loader (запуск вне корня проекта) — безопасные дефолты.
        try:
            from src.config_loader import get_browser_config, get_antidetect_config
            locale = get_browser_config("locale", "ru-RU")
            timezone = get_browser_config("timezone", "Europe/Moscow")
            geoip = bool(get_browser_config("geoip", False))
            humanize = bool(get_antidetect_config("humanize", True))
            self._hide_setters = bool(get_browser_config("hide_setters", True))
            persistent_profile = bool(get_browser_config("persistent_profile", False))
            profile_dir = str(get_browser_config("profile_dir", "data/camoufox_profile"))
            pinned_fingerprint = bool(get_browser_config("pinned_fingerprint", False))
        except Exception:
            locale, timezone, geoip, humanize = "ru-RU", "Europe/Moscow", False, True
            self._hide_setters = True
            persistent_profile, profile_dir, pinned_fingerprint = False, "data/camoufox_profile", False
        if persistent_profile and not pinned_fingerprint:
            logger.warning("persistent_profile=true при pinned_fingerprint=false — "
                           "«тот же пользователь, другой компьютер» (issues #723), "
                           "антибот может усилить проверки")
        # geoip требует camoufox[geoip] (GeoLite2 DB). Если extra не установлен —
        # падаем на явные locale/timezone (та же консистентность для RU-IP).
        if geoip:
            try:
                from camoufox.geolocation import ALLOW_GEOIP
                if not ALLOW_GEOIP:
                    logger.warning("geoip запрошен, но camoufox[geoip] не установлен — "
                                   "использую locale=%s timezone=%s", locale, timezone)
                    geoip = False
            except Exception:
                geoip = False
        if persistent_profile and profile_dir:
            try:
                import pathlib
                pathlib.Path(profile_dir).mkdir(parents=True, exist_ok=True)
            except Exception:
                logger.warning("Не удалось создать профиль %s — использую временный", profile_dir)
        last = None
        for attempt in range(4):
            kwargs = _camoufox_launch_kwargs(
                self.headless, locale=locale, timezone=timezone, geoip=geoip, humanize=humanize,
                persistent_profile=persistent_profile, profile_dir=profile_dir,
                pinned_fingerprint=pinned_fingerprint,
            )
            self._cam = AsyncCamoufox(**kwargs)
            try:
                self._browser = await self._cam.start()
                self._page = await self.new_page()
                return
            except Exception as e:
                last = e
                logger.warning("camoufox start attempt %d failed: %s", attempt + 1, e)
                # Полное завершение неудачной попытки: __aexit__ закрывает браузер,
                # если он успел подняться (иначе ретрай плодит второй экземпляр).
                try:
                    await asyncio.wait_for(self._cam.__aexit__(None, None, None), timeout=15)
                except Exception:
                    logger.warning("camoufox teardown после неудачного старта завис/не удался")
                self._cam = None
                self._browser = None
        raise RuntimeError(f"camoufox start failed after retries: {last}")

    async def stop(self):
        if self._cam is not None:
            try:
                # Таймаут на закрытие: зависший браузер не должен блокировать
                # рестарт bridge (иначе новый браузер поднимется ПОВЕРХ старого).
                await asyncio.wait_for(self._cam.__aexit__(None, None, None), timeout=15)
            except Exception:
                logger.warning("camoufox stop завис/не удался — профиль может быть занят")
        self._cam = None
        self._browser = None
        self._pages = []
        self._page = None

    async def new_page(self):
        page = await self._browser.new_page()
        if self._hide_setters:
            # Убираем перечислимые window.set*Спуферы на каждой навигации.
            # Регистрируется после init-скрипта Camoufox → спуфинг успевает
            # примениться, затем сеттеры удаляются (C++-слой держит значения).
            try:
                await page.add_init_script(_setter_cleanup_script())
            except Exception as e:
                logger.debug("Setter cleanup init script failed (ignored): %s", e)
        page.on("console", lambda msg: self._console_logs.append(f"[{msg.type}] {msg.text}"))
        page.on("response", lambda resp: self._network_requests.append(
            {"url": resp.url, "status": resp.status, "method": resp.request.method}))
        self._pages.append(page)
        self._page = page
        return page

    async def _close_page_impl(self, page):
        await page.close()

    async def goto(self, page, url: str, timeout: int = 30000):
        # Сброс кэша ref снапшота: после перехода на другую страницу ref со старой
        # страницы невалидны. Без сброса click по устаревшему ref резолвился в
        # (роль, имя) со СТАРОЙ страницы → 10с таймаут get_by_role.
        _element_cache.clear()
        try:
            await page.goto(url, wait_until="domcontentloaded", timeout=timeout)
        except Exception as e:
            return f"error: navigation failed: {e}"
        await asyncio.sleep(1.0)
        return None

    async def evaluate(self, page, js: str) -> str:
        js = _wrap_js_if_needed(js)
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
        kind = _resolve_action_target(target)
        if kind[0] == "error":
            return kind[1]
        # Быстрый отказ: до 10с таймаута проверяем наличие элемента в ЖИВОМ DOM.
        # SPA перерисовалась / ref устарел (со старой страницы) → мгновенная ошибка
        # вместо 10с ожидания get_by_role + 2–3 восстановительных LLM-раунда.
        probe = {"css": None, "role": None, "name": ""}
        if kind[0] == "css":
            probe["css"] = kind[1]
        else:
            probe["role"] = kind[1]
            probe["name"] = kind[2]
        info = None
        try:
            info = await page.evaluate(_CLICK_FIND_JS, probe)
        except Exception:
            info = None
        if not info or not info.get("found"):
            hint = _action_error_hint(TimeoutError("Timeout"))
            return ("error: click failed: элемент не найден в DOM по target «%s» — "
                    "страница перерисовалась или ref устарел. Обнови browser_snapshot и "
                    "используй CSS-селектор.%s" % (str(target)[:80], (" — " + hint) if hint else ""))
        before_url = await self.url(page)
        try:
            if kind[0] == "role":
                await page.get_by_role(kind[1], name=kind[2]).click(timeout=10000)
            else:
                await page.click(kind[1], timeout=10000)
            await asyncio.sleep(0.3)
            after_url = await self.url(page)
            if after_url != before_url:
                _element_cache.clear()
            return "ok"
        except Exception as e:
            hint = _action_error_hint(e)
            # Fallback 1: элемент — ссылка → честная навигация на href (без синтетики).
            href = info.get("href") or ""
            if href.startswith("http"):
                try:
                    await page.goto(href, wait_until="domcontentloaded", timeout=20000)
                    await asyncio.sleep(0.5)
                    _element_cache.clear()
                    return "ok (click-fallback: navigated to link href)"
                except Exception as e2:
                    return (f"error: click failed: {e}{(' — ' + hint) if hint else ''} "
                            f"| link-fallback failed: {e2}")
            # Fallback 2: JS force-click (кнопки/дивы — не ссылки). Без LLM-раунда.
            try:
                res = await page.evaluate(_CLICK_FORCE_JS, probe)
                if res and res.get("ok"):
                    await asyncio.sleep(0.5)
                    after_url = await self.url(page)
                    if after_url != before_url:
                        _element_cache.clear()
                    return "ok (click-fallback: force-click)"
                reason = (res or {}).get("reason", "")
                return (f"error: click failed: {e}{(' — ' + hint) if hint else ''} "
                        f"| force-click: {reason}")
            except Exception as e3:
                return (f"error: click failed: {e}{(' — ' + hint) if hint else ''} "
                        f"| force-click error: {e3}")

    async def type_text(self, page, target: str, text: str) -> str:
        kind = _resolve_action_target(target)
        if kind[0] == "error":
            return kind[1]
        try:
            if kind[0] == "role":
                await page.get_by_role(kind[1], name=kind[2]).fill(text, timeout=10000)
            else:
                await page.locator(kind[1]).fill(text, timeout=10000)
            return "ok"
        except Exception as e:
            # Fallback для поисковых полей: роль/CSS не совпали (см. hint ниже).
            # Пытаемся заполнить первый ВИДИМЫЙ поисковый input через JS — это то же,
            # что агент делает вручную browser_evaluate, но без расхода LLM-раунда.
            hint = _action_error_hint(e)
            try:
                filled = await page.evaluate(_SEARCH_INPUT_FILL_JS, text)
                if filled:
                    return f"ok (search-fallback: {filled})"
            except Exception:
                pass
            try:
                fallback = await page.evaluate(_SEARCH_INPUT_FALLBACK_JS)
                if fallback:
                    await page.locator(fallback).fill(text, timeout=5000)
                    return f"ok (last-resort-fallback: {fallback})"
            except Exception:
                pass
            return f"error: type failed: {e}{(' — ' + hint) if hint else ''}"

    async def hover(self, page, target: str) -> str:
        kind = _resolve_action_target(target)
        if kind[0] == "error":
            return kind[1]
        try:
            if kind[0] == "role":
                await page.get_by_role(kind[1], name=kind[2]).hover(timeout=10000)
            else:
                await page.hover(kind[1], timeout=10000)
            return "ok"
        except Exception as e:
            hint = _action_error_hint(e)
            return f"error: hover failed: {e}{(' — ' + hint) if hint else ''}"

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
        _element_cache.clear()
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
        before_url = await self.url(tab)
        try:
            await el.click()
            await asyncio.sleep(0.3)
            after_url = await self.url(tab)
            if after_url != before_url:
                _element_cache.clear()
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
        elif name == "browser_extract":
            try:
                raw = await _driver.evaluate(page, _EXTRACT_SCRIPT)
                if raw.startswith("__JS_ERR__") or not raw.strip():
                    raw = await _driver.evaluate(page, _DOM_SCAN_SCRIPT)
                    return raw
                return raw
            except Exception:
                raw = await _driver.evaluate(page, _DOM_SCAN_SCRIPT)
                return raw
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
