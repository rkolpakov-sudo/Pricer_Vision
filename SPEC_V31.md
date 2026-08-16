# SPEC-005: Pricer_Vision v31.0 — MCP-Agent + GraphDB Architecture

**Version:** 31.0 (MCP-Agent Pipeline)
**Browser:** @playwright/mcp (23 tools, Playwright MCP over stdio)
**LLM Strategy:** Agent-based — LLM autonomously navigates sites using MCP tools + graph memory
**Status:** Implementation — Stable (76-88% success)

---

## Table of Contents

1. [Overview](#1-overview)
2. [Architecture](#2-architecture)
3. [Module Inventory — DELETE vs KEEP vs NEW](#3-module-inventory)
4. [MCP Server: pricer-mcp-server](#4-pricer-mcp-server)
5. [MCP Bridge: mcp_bridge.py](#5-mcp-bridge)
6. [Graph Engine: graph_engine.py](#6-graph-engine)
7. [Memory Manager: memory_manager.py](#7-memory-manager)
8. [Agent Loop: agent_loop.py](#8-agent-loop)
9. [LLM Client: llm_client.py](#9-llm-client)
10. [Tool Parser: tool_parser.py](#10-tool-parser)
11. [Validator: validator.py](#11-validator)
12. [GUI: MainWindow](#12-gui)
13. [GUI: Graph Assistant](#13-graph-assistant)
14. [Pipeline Flow](#14-pipeline-flow)
15. [Data Flow Diagrams](#15-data-flow-diagrams)
16. [Configuration](#16-configuration)
17. [Error Handling & Recovery](#17-error-handling)
18. [Testing Strategy](#18-testing-strategy)
19. [Performance Characteristics](#19-performance)
20. [Migration Path](#20-migration-path)
21. [Appendices](#21-appendices)

---

## 1. Overview

### 1.1 Purpose

Pricer_Vision is an enterprise desktop application that automatically extracts product prices from Russian e-commerce websites. It reads engineering specifications from Excel, navigates supplier websites using a **LLM agent** with **MCP tools** (browser automation + graph knowledge memory), and writes results back to Excel.

### 1.2 Core Innovation

Unlike v30.1 (deterministic 3-level pipeline with hardcoded prompts and YAML selectors), v31.0 uses an **LLM agent** that:

1. **Autonomously explores** supplier sites via nodriver (CDP, anti-detection)
2. **Remembers successful approaches** in a knowledge graph (SQLite + in-memory dicts)
3. **Adapts to site changes** — if a selector breaks, LLM finds a new one and updates the graph
4. **Learns over time** — each confirmed price and successful approach improves future sessions

### 1.3 Key Principles

- **No hardcoded strategies** — LLM decides how to find prices
- **No site_overrides.yaml** — approaches live in the graph
- **No instructions.md** — system prompt documents tool APIs only, not action sequences
- **No schema.json** — LLM chooses response format
- **Graph is memory, not rules** — approaches are suggestions, LLM adapts per session
- **YAML is seed data only** — LLM can discover sites outside YAML and save them to the graph

### 1.4 Technology Stack

| Component | Version | Purpose |
|---|---|---|
| Python | 3.14+ | Runtime |
| PySide6 | >=6.6.0 | Desktop GUI |
| @playwright/mcp | latest | MCP browser automation (23 tools, over stdio) |
| mcp (Python SDK) | >=1.0 | MCP protocol: client |
| openpyxl | >=3.1.2 | Excel I/O |
| PyYAML | >=6.0.1 | Config parsing |
| httpx | >=0.27 | Async HTTP for LLM API |
| LM Studio | latest | Local LLM inference (Qwen 2.5 7B-32B) |
| SQLite | 3.x | Graph persistence |
| pytest | >=8.0 | Testing |

### 1.5 Quick Start

```powershell
cd C:\Projects\Pricer_Vision
.\venv\Scripts\activate
python main.py
```

MCP server starts automatically when processing begins. Graph DB auto-creates on first run.

---

## 2. Architecture

### 2.1 High-Level Architecture

```
┌─────────────────────────────────────────────────────────────────────┐
│  GUI Layer (PySide6)                                                 │
│  ┌─────────────────────────────────────────────────────────────┐    │
│  │  MainWindow (main.py)                                        │    │
│  │  ├── Toolbar (start/stop/pause)                              │    │
│  │  ├── Excel input/output                                      │    │
│  │  ├── Results table                                           │    │
│  │  ├── Log panel                                               │    │
│  │  ├── Graph Assistant (11-tool + study)                       │    │
│  │  └── Status bar (LLM status, graph status)                   │    │
│  │                                                               │    │
│  │  EngineWorker (QThread)                                       │    │
│  │  └── MCPAgentRunner (asyncio event loop)                     │    │
│  └─────────────────────────────────────────────────────────────┘    │
└──────────────────────────────────────────┬──────────────────────────┘
                                             │ signals: progress, result, done
                                             ▼
┌─────────────────────────────────────────────────────────────────────┐
│  Agent Layer (MCPAgentRunner — asyncio)                              │
│                                                                       │
│  ┌────────────────────────────────────────────────────────────┐      │
│  │  LLMClient (httpx → LM Studio)                              │      │
│  │  Provider: LM Studio @ http://localhost:1234/v1             │      │
│  │  Protocol: OpenAI-compatible chat completions + tool_calls   │      │
│  └───────────────────┬────────────────────────────────────────┘      │
│                      │ tool_calls + response                          │
│                      ▼                                                │
│  ┌────────────────────────────────────────────────────────────┐      │
│  │  agent_loop.py — run_tool_loop                              │      │
│  │  Pattern: 3-branch routing                                  │      │
│  │  Algorithm:                                                  │      │
│  │    1. LLM call with tools                                   │      │
│  │    2. Parse tool_calls from response                        │      │
│  │    3. Route: GRAPH_TOOL → execute, navigate → MCP, else→MCP │      │
│  │    4. Execute tool, get result                              │      │
│  │    5. Append result to messages                             │      │
│  │    6. Re-prompt LLM w/updated messages + tools              │      │
│  │    7. Repeat up to 50 rounds                                │      │
│  │    8. Force switch site after 15 rounds without result       │      │
│  └──────┬──────────────────────────────┬──────────────────────┘      │
│         │                              │                             │
│         ▼                              ▼                             │
│  ┌──────────────┐             ┌──────────────────┐                   │
│  │ mcp_bridge   │             │ graph_engine     │                   │
│  │ (MCP client) │             │ (SQLite + dicts) │                   │
│  │              │             │                  │                   │
│  │ manages      │             │ loads index at   │                   │
│  │ Playwright   │             │ startup (lazy)   │                   │
│  │ MCP server   │             │ inc-cache writes │                   │
│  └───────┬──────┘             └──────────────────┘                   │
│          │ JSON-RPC over stdio                                       │
│          ▼                                                           │
│  ┌────────────────────────────────────────────────────────────┐      │
│  │  npx.cmd @playwright/mcp (subprocess)                       │      │
│  │  MCP protocol server wrapping Playwright                    │      │
│  │  Tools (23): browser_navigate, browser_snapshot,            │      │
│  │  browser_click, browser_type, browser_evaluate, etc.        │      │
│  │  Anti-detection: stealth.js (12 patches) + playwright-mcp   │      │
│  └────────────────────────────────────────────────────────────┘      │
└─────────────────────────────────────────────────────────────────────┘
```

### 2.2 Module Dependency Graph

```
main.py
  ├── gui/main_window.py          (new, refactored from old main.py)
  │     ├── gui/graph_assistant.py  (new, patterned after Norm_graph assistant)
  │     ├── src/theme.py            (keep)
  │     ├── src/toast.py            (keep)
  │     ├── src/widget_base.py      (keep)
  │     └── src/excel_writer.py     (keep)
  │
  └── src/mcp_agent_runner.py     (new — QThread wrapper)
        ├── src/agent_loop.py      (new — run_tool_loop, asyncio)
        │     ├── src/llm_client.py   (new — httpx → LM Studio)
        │     ├── src/tool_parser.py  (new — parse_tool_calls + final response)
        │     ├── src/mcp_bridge.py   (new — MCP client lifecycle)
        │     │     └── pricer-mcp-server (new — subprocess, nodriver MCP wrapper)
        │     ├── src/graph_engine.py (new — SQLite + dicts)
        │     └── src/validator.py    (new — confidence + anomaly checks)
        └── src/checkpoint.py      (adapt)
```

### 2.3 Threading Model

```
Main Thread (PySide6 GUI):
  - All UI rendering, event loop
  - NEVER blocks for I/O

EngineWorker (QThread):
  - Own asyncio event loop
  - Runs MCPAgentRunner.process_spec()
  - Communicates via Qt signals:
    - status_signal(str | tuple) — log messages, progress
    - row_done_signal(int, dict) — per-row result
    - done_signal(bool, SpecResult) — batch complete
    - error_signal(str) — fatal error

pricer-mcp-server (subprocess):
  - Started/stopped by MCPBridge
  - Separate process: Chrome + nodriver
  - Communicates via JSON-RPC over stdio
  - Can be restarted independently

Graph Engine:
  - SQLite connections in main thread (reads) + worker thread (writes)
  - Thread-safe via threading.Lock or connection-per-thread
```

---

## 3. Module Inventory: ACTUAL (2026-07-02)

### Ключевые изменения против спецификации

| Изменение | Причина |
|-----------|---------|
| `@playwright/mcp` вместо nodriver | Playwright MCP — 23 production-grade tools |
| `config/stealth.js` — 12 патчей | Anti-detection при переходе от nodriver |
| `mcp_bridge.py` — один сервер (playwright) | Pricer/DrissionPage сервер удалён |
| `patchright_server.py` — не используется | Замена @playwright/mcp отменена (регрессия) |
| Tool routing: 3 ветки вместо 1 | Yandex Rule 12 guard сломал routing |
| `MAX_ROUNDS=50` (из settings.yaml) | 40 не хватало |
| `format_steps()` показывает URL | Подходы в контексте показывали пустые `[]` |
| `unknown` исключён из `_load_indexes` | Тип-пустышка засорял UI |
| Study Runner: 50 раундов, get_hints | Агенту не хватало инструментов и раундов |

---

## 4. MCP Server: @playwright/mcp

### 4.1 Overview

Production-grade MCP server from Microsoft. Wraps Playwright (Chromium automation library). Runs as subprocess via `npx.cmd @playwright/mcp`, communicates via JSON-RPC over stdio. Exposes 23 browser automation tools to the LLM agent.

### 4.2 Technology

- **Protocol:** MCP (Model Context Protocol) — JSON-RPC 2.0 over stdio
- **SDK:** npx @playwright/mcp — official Microsoft MCP server
- **Browser:** Playwright Chromium (installed via `npx playwright install chromium`)
- **Anti-detection:** stealth.js (12 CDP patches) + playwright-mcp.json config
- **Auto-start:** MCPBridge starts it as subprocess when processing begins

### 4.3 Configuration

```json
// config/playwright-mcp.json
{
  "browser": {
    "launchOptions": {
      "args": [
        "--no-sandbox",
        "--disable-session-crashed-bubble",
        "--disable-infobars",
        "--disable-notifications",
        "--no-first-run",
        "--disable-default-apps",
        "--hide-scrollbars"
      ]
    }
  }
}
```

### 4.4 Tool Inventory (23)

| Tool | Description |
|---|---|
| `browser_navigate(url)` | Navigate to URL, return accessibility tree |
| `browser_snapshot()` | Accessibility tree snapshot |
| `browser_click(target)` | Click element (CSS selector or role locator) |
| `browser_type(target, text)` | Type text into input |
| `browser_press_key(key)` | Press keyboard key |
| `browser_wait_for(ms)` | Wait for ms |
| `browser_evaluate(function)` | Execute JavaScript in page context |
| `browser_run_code_unsafe(code)` | Execute arbitrary JavaScript |
| `browser_take_screenshot()` | Take screenshot |
| `browser_close()` | Close current page |
| `browser_resize(w, h)` | Resize viewport |
| `browser_console_messages()` | Get console messages |
| `browser_handle_dialog(action)` | Handle dialog |
| `browser_file_upload(selector, files)` | Upload files |
| `browser_drag(source, target)` | Drag element |
| `browser_fill_form(selector, values)` | Fill form |
| `browser_navigate_back()` | Go back |
| `browser_network_requests()` | Get network requests |
| `browser_network_request(url)` | Get specific request |
| `browser_tabs()` | List all tabs |
| `browser_hover(target)` | Hover over element |
| `browser_select_option(selector, values)` | Select option |
| `browser_drop(source, target)` | Drop element |

### 4.5 Anti-Detection

| Measure | Implementation |
|---|---|
| `navigator.webdriver` | Patched by stealth.js |
| `navigator.plugins` | Patched with real Chrome plugin names |
| `navigator.languages` | `['ru-RU', 'ru', 'en-US', 'en']` |
| `navigator.hardwareConcurrency` | 8 |
| `navigator.deviceMemory` | 8 |
| `chrome.runtime` | Patched with runtime, loadTimes, csi, app |
| `WebGL vendor/renderer` | Intel Open Source + Mesa DRI |
| `navigator.permissions` | query() returns Notification.permission |
| `navigator.connection` | rtt=50, downlink=10, effectiveType=4g |
| `screen` | colorDepth=24, pixelDepth=24 |
| `navigator.platform` | Win32 |
| `navigator.mediaDevices` | enumerateDevices returns 3 devices |
| `navigator.getBattery` | Returns charging=true, level=1 |
| `--disable-blink-features=AutomationControlled` | NOT used (detectable by DDoS-Guard) |

---

## 5. mcp_bridge.py

### 5.1 Overview

Manages the lifecycle of the pricer-mcp-server subprocess. Translates between the agent loop (OpenAI tool_call format) and MCP protocol (JSON-RPC over stdio).

### 5.2 Interface

```python
class MCPBridge:
    def __init__(self):
        self._process: asyncio.subprocess.Process | None = None
        self._session: ClientSession | None = None
        self._lock = asyncio.Lock()
    
    async def start(self) -> bool:
        """Start pricer-mcp-server as subprocess. Initialize MCP session."""
        ...
    
    async def call_tool(self, tool_name: str, arguments: dict) -> str:
        """Call MCP tool. Returns result string or "error: ..."."""
        async with self._lock:
            ...
    
    async def health_check(self) -> bool:
        """Ping MCP server. Returns True if alive."""
        ...
    
    async def restart(self) -> bool:
        """Kill and restart subprocess."""
        ...
    
    async def stop(self):
        """Close session, kill process."""
        ...
```

### 5.3 Implementation Details

```python
import asyncio
from mcp import ClientSession, StdioClient

class MCPBridge:
    def __init__(self):
        self._process = None
        self._session = None
        self._lock = asyncio.Lock()
    
    async def start(self) -> bool:
        try:
            self._process = await asyncio.create_subprocess_exec(
                sys.executable, "-m", "pricer_mcp_server",
                stdin=asyncio.subprocess.PIPE,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )
            transport = StdioClient(
                self._process.stdin,
                self._process.stdout
            )
            self._session = await ClientSession(transport)
            await self._session.initialize()
            return True
        except Exception as e:
            logger.error(f"MCPBridge.start failed: {e}")
            return False
    
    async def call_tool(self, tool_name: str, arguments: dict) -> str:
        if not self._session:
            return "error: bridge not initialized"
        async with self._lock:
            try:
                result = await self._session.call_tool(tool_name, arguments)
                # result.content is list[TextContent | ImageContent | ...]
                parts = []
                for content in result.content:
                    if hasattr(content, "text"):
                        parts.append(content.text)
                    elif hasattr(content, "data"):
                        parts.append(f"[binary data: {len(content.data)} bytes]")
                return "\n".join(parts)
            except Exception as e:
                return f"error: tool call failed: {e}"
    
    async def health_check(self) -> bool:
        try:
            if self._session:
                await self._session.ping()
                return True
            return False
        except Exception:
            return False
    
    async def restart(self) -> bool:
        await self.stop()
        await asyncio.sleep(1.0)
        return await self.start()
    
    async def stop(self):
        try:
            if self._session:
                await self._session.close()
        except Exception:
            pass
        try:
            if self._process:
                self._process.kill()
                await self._process.wait()
        except Exception:
            pass
        self._session = None
        self._process = None
```

### 5.4 Error Handling

| Error | Action |
|---|---|
| start() returns False | agent_loop retries 2×, then fails with "MCP server unavailable" |
| call_tool() returns "error: ..." | agent_loop reports to LLM, LLM decides next action |
| health_check() returns False | MCPBridge.restart(), agent_loop pauses 3s |
| Subprocess crash | MCPBridge detects via health_check, auto-restart |
| Session closed | Same as crash — restart |

---

## 6. graph_engine.py

### 6.1 Overview

The knowledge graph persistence layer. Stores and retrieves:
- **approaches** — successful sequences of browser actions
- **confirmed_prices** — verified price records (used as few-shot examples)
- **hints** — textual hints for product types and sites
- **sites** — known supplier sites (from YAML seed + discovered)

Uses SQLite for persistence and in-memory Python dicts for fast access.

### 6.2 SQLite Schema

```sql
-- Product types (from YAML seed + auto-discovered)
CREATE TABLE IF NOT EXISTS product_types (
    id TEXT PRIMARY KEY,                  -- 'ups', 'cable_vvg', 'vfd'
    name TEXT NOT NULL,                   -- 'Источники бесперебойного питания'
    category TEXT,                        -- 'electrical'
    keywords TEXT,                        -- 'ИБП, UPS, бесперебойник, источник БП'
    created_at TEXT DEFAULT (datetime('now'))
);

-- Sites (from YAML seed + auto-discovered via yandex)
CREATE TABLE IF NOT EXISTS sites (
    id TEXT PRIMARY KEY,                  -- 'satro-paladin.com'
    name TEXT NOT NULL,                   -- 'Сатро-Паладин'
    base_url TEXT,
    group_name TEXT,                      -- 'supplier', 'marketplace'
    source TEXT DEFAULT 'yaml',           -- 'yaml', 'auto_discovered'
    created_at TEXT DEFAULT (datetime('now'))
);

-- Product-site relationships (which product types sold at which sites)
CREATE TABLE IF NOT EXISTS product_sites (
    product_type_id TEXT REFERENCES product_types(id),
    site_id TEXT REFERENCES sites(id),
    priority INTEGER DEFAULT 0,           -- 0=all, 1=secondary, 2=primary
    PRIMARY KEY (product_type_id, site_id)
);

-- Approaches: successful browser action sequences
CREATE TABLE IF NOT EXISTS approaches (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    product_type_id TEXT REFERENCES product_types(id),
    site_id TEXT REFERENCES sites(id),
    
    -- Core data
    pattern TEXT NOT NULL,                -- JSON: abstract pattern steps
    concrete TEXT NOT NULL,               -- JSON: concrete steps with selectors
    selectors_cache TEXT,                 -- JSON: {target: {primary, fallbacks[]}}
    param_slots TEXT,                     -- JSON: {param_name: {type, description}}
    
    -- Metadata
    method TEXT,                          -- 'search_then_navigate', 'direct_url', 'catalog_browse'
    search_query TEXT,                    -- what was typed in search
    
    -- Temporal
    success_count INTEGER DEFAULT 1,
    failures_count INTEGER DEFAULT 0,
    consecutive_failures INTEGER DEFAULT 0,
    cooldown_until TEXT,                  -- ISO datetime or NULL
    is_deprecated INTEGER DEFAULT 0,
    last_success_date TEXT,               -- ISO date
    last_failure_date TEXT,               -- ISO date
    
    notes TEXT,
    created_at TEXT DEFAULT (datetime('now'))
);

-- Confirmed prices (user-verified or high-confidence auto)
CREATE TABLE IF NOT EXISTS confirmed_prices (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    spec_text TEXT NOT NULL,
    product_type_id TEXT REFERENCES product_types(id),
    site_id TEXT REFERENCES sites(id),
    price REAL NOT NULL,
    currency TEXT DEFAULT 'RUB',
    url TEXT,
    confidence REAL DEFAULT 0.95,
    source TEXT DEFAULT 'agent',           -- 'agent', 'user_correction'
    reason TEXT,
    created_at TEXT DEFAULT (datetime('now'))
);

-- Hints (textual hints for product types and sites)
CREATE TABLE IF NOT EXISTS hints (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    product_type_id TEXT REFERENCES product_types(id),
    site_id TEXT,                          -- NULL = applies to all sites for this product
    hint_text TEXT NOT NULL,
    priority REAL DEFAULT 0.5,
    created_at TEXT DEFAULT (datetime('now'))
);

-- Concept relationships (IS_A / EQUIVALENT_TO)
CREATE TABLE IF NOT EXISTS concepts (
    name TEXT PRIMARY KEY,
    description TEXT,
    source TEXT DEFAULT 'user',
    created_at TEXT DEFAULT (datetime('now'))
);

CREATE TABLE IF NOT EXISTS concept_edges (
    child_name TEXT REFERENCES concepts(name),
    parent_name TEXT REFERENCES concepts(name),
    relation TEXT CHECK(relation IN ('IS_A', 'EQUIVALENT_TO')),
    weight REAL DEFAULT 1.0,
    PRIMARY KEY (child_name, parent_name, relation)
);

-- Indexes
CREATE INDEX IF NOT EXISTS idx_approaches_product_site ON approaches(product_type_id, site_id);
CREATE INDEX IF NOT EXISTS idx_approaches_site ON approaches(site_id);
CREATE INDEX IF NOT EXISTS idx_confirmed_spec ON confirmed_prices(spec_text);
CREATE INDEX IF NOT EXISTS idx_confirmed_product ON confirmed_prices(product_type_id);
CREATE INDEX IF NOT EXISTS idx_hints_product ON hints(product_type_id);
```

### 6.3 In-Memory Indexes

Built at startup from SQLite. `build()` is called lazily (first access).

```python
class GraphEngine:
    def __init__(self, db_path: str):
        self.db_path = db_path
        self._conn: sqlite3.Connection | None = None
        self._lock = threading.Lock()
        
        # In-memory indexes (built by build())
        self._approaches_index: dict[tuple[str, str], list[dict]] = {}   # (product, site) → approaches
        self._approaches_by_product: dict[str, list[dict]] = {}          # product → approaches
        self._product_sites: dict[str, list[str]] = {}                   # product → [sites]
        self._prices_by_spec_token: dict[str, list[dict]] = {}           # token → prices
        self._hints_by_product: dict[str, list[dict]] = {}               # product → hints
        self._all_sites: dict[str, dict] = {}                            # site_id → site data
        self._all_products: dict[str, dict] = {}                         # product_type_id → data
        self._built = False
    
    def build(self):
        """Load all data from SQLite into in-memory indexes. Thread-safe."""
        with self._lock:
            if self._built:
                return
            self._conn = sqlite3.connect(self.db_path, check_same_thread=False)
            self._conn.row_factory = sqlite3.Row
            self._conn.execute("PRAGMA journal_mode=WAL")
            self._conn.execute("PRAGMA foreign_keys=ON")
            self._create_tables()
            self._load_indexes()
            self._built = True
    
    def _create_tables(self):
        """Execute CREATE TABLE IF NOT EXISTS for all tables."""
        # (executes the SQL from 6.2)
    
    def _load_indexes(self):
        """Load all data into in-memory dicts."""
        # Approaches
        self._approaches_index.clear()
        self._approaches_by_product.clear()
        for row in self._conn.execute(
            "SELECT * FROM approaches WHERE is_deprecated = 0 AND "
            "(cooldown_until IS NULL OR cooldown_until < datetime('now'))"
        ):
            a = dict(row)
            key = (a["product_type_id"], a["site_id"])
            self._approaches_index.setdefault(key, []).append(a)
            self._approaches_by_product.setdefault(a["product_type_id"], []).append(a)
        
        # Confirmed prices: build token index for few-shot lookup
        self._prices_by_spec_token.clear()
        import re
        for row in self._conn.execute("SELECT * FROM confirmed_prices"):
            p = dict(row)
            tokens = set(re.findall(r'\w+', p["spec_text"].lower()))
            for token in tokens:
                if len(token) > 2:
                    self._prices_by_spec_token.setdefault(token, []).append(p)
        
        # Product→sites
        self._product_sites.clear()
        for row in self._conn.execute(
            "SELECT ps.*, s.name as site_name, s.base_url FROM product_sites ps "
            "JOIN sites s ON s.id = ps.site_id"
        ):
            self._product_sites.setdefault(row["product_type_id"], []).append({
                "id": row["site_id"],
                "name": row["site_name"],
                "base_url": row["base_url"],
                "priority": row["priority"],
            })
        
        # Hints
        self._hints_by_product.clear()
        for row in self._conn.execute("SELECT * FROM hints ORDER BY priority DESC"):
            self._hints_by_product.setdefault(row["product_type_id"], []).append(dict(row))
        
        # Sites
        self._all_sites.clear()
        for row in self._conn.execute("SELECT * FROM sites"):
            self._all_sites[row["id"]] = dict(row)
        
        # Products
        self._all_products.clear()
        for row in self._conn.execute("SELECT * FROM product_types"):
            self._all_products[row["id"]] = dict(row)
```

### 6.4 Public API Methods

```python
# ── Approach operations ──

def get_approaches(self, product_type: str, site: str | None = None) -> list[dict]:
    """Get all non-deprecated, non-cooldown approaches. If site specified — filter by site."""
    self.build()
    if site:
        return self._approaches_index.get((product_type, site), [])
    return self._approaches_by_product.get(product_type, [])

def get_best_approach(self, product_type: str, site: str) -> dict | None:
    """Get highest-success-count, non-deprecated approach."""
    approaches = self.get_approaches(product_type, site)
    if not approaches:
        return None
    # Sort by success_count desc, failures_count asc, freshness desc
    from datetime import datetime
    def sort_key(a):
        freshness = 1.0
        if a.get("last_success_date"):
            try:
                days_since = (datetime.now() - datetime.fromisoformat(a["last_success_date"])).days
                freshness = max(0.1, 1.0 - days_since / 30.0)
            except (ValueError, TypeError):
                freshness = 0.5
        return (a.get("success_count", 0) * 0.5 + freshness * 0.3 - a.get("consecutive_failures", 0) * 0.2)
    approaches_sorted = sorted(approaches, key=sort_key, reverse=True)
    return approaches_sorted[0]

def save_approach(self, data: dict) -> int:
    """Save approach to SQLite and update in-memory index."""
    self.build()
    with self._lock:
        cur = self._conn.execute(
            """INSERT INTO approaches 
            (product_type_id, site_id, pattern, concrete, selectors_cache, 
            param_slots, method, search_query, notes)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (
                data["product_type_id"], data["site_id"],
                json.dumps(data.get("pattern", []), ensure_ascii=False),
                json.dumps(data.get("concrete", []), ensure_ascii=False),
                json.dumps(data.get("selectors_cache", {}), ensure_ascii=False),
                json.dumps(data.get("param_slots", {}), ensure_ascii=False),
                data.get("method", ""), data.get("search_query", ""),
                data.get("notes", ""),
            )
        )
        self._conn.commit()
        approach_id = cur.lastrowid
        # Update in-memory index
        approach_data = {
            "id": approach_id,
            "product_type_id": data["product_type_id"],
            "site_id": data["site_id"],
            "pattern": data.get("pattern", []),
            "concrete": data.get("concrete", []),
            "selectors_cache": data.get("selectors_cache", {}),
            "param_slots": data.get("param_slots", {}),
            "method": data.get("method", ""),
            "search_query": data.get("search_query", ""),
            "success_count": 1, "failures_count": 0,
            "consecutive_failures": 0, "is_deprecated": 0,
            "last_success_date": datetime.now().isoformat(),
            "notes": data.get("notes", ""),
        }
        key = (data["product_type_id"], data["site_id"])
        self._approaches_index.setdefault(key, []).insert(0, approach_data)
        self._approaches_by_product.setdefault(data["product_type_id"], []).insert(0, approach_data)
        return approach_id

def update_approach_success(self, approach_id: int):
    """Increment success_count, reset failures, update dates."""
    self.build()
    with self._lock:
        now = datetime.now().isoformat()
        self._conn.execute(
            "UPDATE approaches SET success_count = success_count + 1, "
            "consecutive_failures = 0, last_success_date = ?, "
            "cooldown_until = NULL WHERE id = ?",
            (now, approach_id)
        )
        self._conn.commit()
    # Update cache (simplified — could rebuild)
    self._built = False  # Force rebuild on next access

def update_approach_failure(self, approach_id: int):
    """Increment failures, potentially set cooldown or deprecate."""
    self.build()
    with self._lock:
        row = self._conn.execute(
            "SELECT consecutive_failures FROM approaches WHERE id = ?",
            (approach_id,)
        ).fetchone()
        if not row:
            return
        new_failures = row["consecutive_failures"] + 1
        now = datetime.now().isoformat()
        cooldown = None
        deprecated = 0
        if new_failures >= 10:
            deprecated = 1
        elif new_failures >= 3:
            from datetime import timedelta
            cooldown = (datetime.now() + timedelta(hours=24)).isoformat()
        self._conn.execute(
            "UPDATE approaches SET failures_count = failures_count + 1, "
            "consecutive_failures = ?, cooldown_until = ?, "
            "is_deprecated = ?, last_failure_date = ? WHERE id = ?",
            (new_failures, cooldown, deprecated, now, approach_id)
        )
        self._conn.commit()
    self._built = False  # Force rebuild


# ── Confirmed Price operations ──

def get_confirmed_prices(self, spec_text: str, max_results: int = 5) -> list[dict]:
    """Find confirmed prices with token overlap >= 2.
    Prices older than 30 days get confidence × 0.65 + is_stale=True flag."""
    self.build()
    import re
    spec_tokens = {t.lower() for t in re.findall(r'\w+', spec_text) if len(t) > 2}
    if not spec_tokens:
        return []
    
    # Collect candidates by token overlap
    candidates = {}
    for token in spec_tokens:
        for price in self._prices_by_spec_token.get(token, []):
            pid = price["id"]
            if pid not in candidates:
                candidates[pid] = {"price": price, "overlap": 0}
            candidates[pid]["overlap"] += 1
    
    # Score and sort
    scored = []
    for pid, info in candidates.items():
        overlap = info["overlap"]
        if overlap >= 2:
            scored.append((overlap, info["price"]))
    
    scored.sort(key=lambda x: -x[0] * x[1].get("confidence", 0.5))
    return [p for _, p in scored[:max_results]]

def save_confirmed_price(self, data: dict) -> int:
    """Save confirmed price to SQLite and update token index."""
    self.build()
    with self._lock:
        cur = self._conn.execute(
            """INSERT INTO confirmed_prices 
            (spec_text, product_type_id, site_id, price, currency, url, confidence, source, reason)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (
                data["spec_text"], data["product_type_id"], data["site_id"],
                data["price"], data.get("currency", "RUB"),
                data.get("url", ""), data.get("confidence", 0.95),
                data.get("source", "agent"), data.get("reason", ""),
            )
        )
        self._conn.commit()
        price_id = cur.lastrowid
        # Update token index
        import re
        tokens = set(re.findall(r'\w+', data["spec_text"].lower()))
        price_entry = {
            "id": price_id, "spec_text": data["spec_text"],
            "price": data["price"], "site_id": data["site_id"],
            "confidence": data.get("confidence", 0.95),
            "url": data.get("url", ""),
        }
        for token in tokens:
            if len(token) > 2:
                self._prices_by_spec_token.setdefault(token, []).append(price_entry)
        return price_id


# ── Hint operations ──

def get_hints(self, product_type: str) -> list[dict]:
    """Get hints for product type."""
    self.build()
    return self._hints_by_product.get(product_type, [])

def save_hint(self, product_type: str, site: str | None, text: str, priority: float = 0.5) -> int:
    self.build()
    with self._lock:
        cur = self._conn.execute(
            "INSERT INTO hints (product_type_id, site_id, hint_text, priority) VALUES (?, ?, ?, ?)",
            (product_type, site, text, priority)
        )
        self._conn.commit()
    self._built = False
    return cur.lastrowid


# ── Site operations ──

def get_sites_for_product(self, product_type: str) -> list[dict]:
    """Get all known sites for this product type. Combines YAML seed + auto-discovered."""
    self.build()
    return self._product_sites.get(product_type, [])

def save_discovered_site(self, domain: str, name: str, product_type: str) -> str:
    """Save a site found by the agent (not from YAML)."""
    self.build()
    with self._lock:
        # Check if site exists
        existing = self._conn.execute("SELECT id FROM sites WHERE id = ?", (domain,)).fetchone()
        if not existing:
            self._conn.execute(
                "INSERT INTO sites (id, name, base_url, source) VALUES (?, ?, ?, 'auto_discovered')",
                (domain, name, f"https://{domain}")
            )
        # Link to product
        self._conn.execute(
            "INSERT OR IGNORE INTO product_sites (product_type_id, site_id, priority) VALUES (?, ?, 0)",
            (product_type, domain)
        )
        self._conn.commit()
    self._built = False
    return domain


# ── Product type operations ──

def classify_product_type(self, spec_text: str) -> str:
    """Determine product type from spec text using keyword matching."""
    self.build()
    spec_lower = spec_text.lower()
    best_match = None
    best_score = 0
    for pid, pdata in self._all_products.items():
        keywords = pdata.get("keywords", "").lower()
        score = sum(1 for kw in keywords.split(",") if kw.strip() in spec_lower)
        if score > best_score:
            best_score = score
            best_match = pid
    return best_match or "unknown"


# ── Statistics ──

def get_stats(self) -> dict:
    """Get graph statistics."""
    self.build()
    with self._lock:
        approaches = self._conn.execute("SELECT COUNT(*) FROM approaches").fetchone()[0]
        prices = self._conn.execute("SELECT COUNT(*) FROM confirmed_prices").fetchone()[0]
        hints = self._conn.execute("SELECT COUNT(*) FROM hints").fetchone()[0]
        sites = self._conn.execute("SELECT COUNT(*) FROM sites").fetchone()[0]
        products = self._conn.execute("SELECT COUNT(*) FROM product_types").fetchone()[0]
    return {
        "approaches": approaches, "confirmed_prices": prices,
        "hints": hints, "sites": sites, "product_types": products,
    }
```

---

## 7. memory_manager.py

### 7.1 Overview

CRUD operations for the knowledge graph. Thin layer over GraphEngine with additional business logic:
- Deduplication (keep oldest, remove duplicates)
- Token-based relevance search (for few-shot)
- Scoring with freshness decay

### 7.2 Interface

```python
from src.graph_engine import GraphEngine
import re
from datetime import datetime

class MemoryManager:
    def __init__(self, engine: GraphEngine):
        self._engine = engine
    
    # ── Approaches ──
    
    def get_best_approach(self, product_type: str, site: str) -> dict | None:
        """Get best approach with freshness scoring."""
        return self._engine.get_best_approach(product_type, site)
    
    def get_all_approaches(self, product_type: str) -> list[dict]:
        """Get all approaches for a product type."""
        return self._engine.get_approaches(product_type)
    
    def get_site_approaches(self, product_type: str, site: str) -> list[dict]:
        """Get approaches for a specific site."""
        return self._engine.get_approaches(product_type, site)
    
    def save_approach(self, product_type: str, site: str, concrete_steps: list,
                      selectors_cache: dict | None = None,
                      param_slots: dict | None = None,
                      method: str = "", search_query: str = "",
                      notes: str = "") -> int:
        """
        Save a new successful approach. 
        concrete_steps: [{"action": "navigate", "url": "..."}, {"action": "click", "target": "...", "target_type": "css_selector"}, ...]
        selectors_cache: {"search_input": {"primary": "#search", "fallbacks": [".search-input"]}}
        param_slots: {"product_name": {"type": "string", "description": "название для поиска"}}
        """
        # Derive pattern from concrete steps
        pattern = []
        for step in concrete_steps:
            pattern_step = {"action": step.get("action"), "configurable": False}
            if step.get("param_slot"):
                pattern_step["configurable"] = True
                pattern_step["param"] = step["param_slot"]
            pattern.append(pattern_step)
        
        return self._engine.save_approach({
            "product_type_id": product_type,
            "site_id": site,
            "pattern": pattern,
            "concrete": concrete_steps,
            "selectors_cache": selectors_cache or {},
            "param_slots": param_slots or {},
            "method": method,
            "search_query": search_query,
            "notes": notes,
        })
    
    def record_failure(self, approach_id: int):
        """Record a failed attempt (selector not found, etc)."""
        self._engine.update_approach_failure(approach_id)
    
    def record_success(self, approach_id: int):
        """Record a successful reuse of approach."""
        self._engine.update_approach_success(approach_id)
    
    # ── Confirmed Prices ──
    
    def get_relevant_prices(self, spec_text: str, max_results: int = 5) -> list[dict]:
        """Get confirmed prices with token overlap >= 2 (few-shot examples)."""
        return self._engine.get_confirmed_prices(spec_text, max_results)
    
    def save_price(self, spec_text: str, product_type: str, site: str,
                   price: float, url: str, confidence: float,
                   reason: str = "", source: str = "agent") -> int:
        """Save a confirmed price. Only if confidence > 0.6."""
        if confidence < 0.6:
            return 0
        return self._engine.save_confirmed_price({
            "spec_text": spec_text,
            "product_type_id": product_type,
            "site_id": site,
            "price": price,
            "url": url,
            "confidence": min(confidence, 1.0),
            "source": source,
            "reason": reason,
        })
    
    # ── Hints ──
    
    def get_hints(self, product_type: str) -> list[dict]:
        return self._engine.get_hints(product_type)
    
    def add_hint(self, product_type: str, text: str, 
                 site: str | None = None, priority: float = 0.5) -> int:
        return self._engine.save_hint(product_type, site, text, priority)
    
    # ── Sites ──
    
    def get_sites(self, product_type: str) -> list[dict]:
        return self._engine.get_sites_for_product(product_type)
    
    def add_site(self, domain: str, name: str, product_type: str):
        return self._engine.save_discovered_site(domain, name, product_type)
    
    # ── Concepts (SOLD_AT edges) ──

    def save_concept_edge(self, child: str, parent: str, relation: str = "SOLD_AT", weight: float = 1.0):
        """
        Save a concept edge between child and parent.
        Thread-safe: uses engine._lock.
        Creates concepts + edge in a single transaction.
        Used by StudyPage (previously raw SQL) and agent_loop (record_soldat).
        """
        engine = self._engine
        try:
            with engine._lock:
                engine._conn.execute(
                    "INSERT OR IGNORE INTO concepts (name, description, source) VALUES (?, ?, 'study')",
                    (child, f"study relation: {relation} {parent}")
                )
                engine._conn.execute(
                    "INSERT OR IGNORE INTO concepts (name, description, source) VALUES (?, ?, 'study')",
                    (parent, f"study relation: child {child}")
                )
                engine._conn.execute(
                    "INSERT OR REPLACE INTO concept_edges (child_name, parent_name, relation, weight) VALUES (?, ?, ?, ?)",
                    (child, parent, relation, weight)
                )
                engine._conn.commit()
        except Exception as e:
            logger.warning("Failed to save concept edge %s %s %s: %s", child, relation, parent, e)
    
    # ── Deduplication (self-heal) ──
    
    def deduplicate_approaches(self) -> int:
        """Remove duplicate approaches (same product + site + concrete steps)."""
        # SQL: keep MIN(id) per group
        conn = self._engine._conn
        deleted = conn.execute("""
            DELETE FROM approaches WHERE id NOT IN (
                SELECT MIN(id) FROM approaches GROUP BY product_type_id, site_id, concrete
            )
        """).rowcount
        conn.commit()
        return deleted
    
    def deduplicate_prices(self) -> int:
        """Remove duplicate confirmed prices (same spec + site + price)."""
        conn = self._engine._conn
        deleted = conn.execute("""
            DELETE FROM confirmed_prices WHERE id NOT IN (
                SELECT MIN(id) FROM confirmed_prices GROUP BY spec_text, site_id, price
            )
        """).rowcount
        conn.commit()
        return deleted
```

---

## 8. agent_loop.py

### 8.1 Overview

The core processing loop. Runs asynchronously, orchestrating LLM calls, MCP browser tools, and graph memory. Pattern sourced from Norm_graph's `run_tool_loop`.

### 8.2 Full Tool Definitions (fed to LLM)

```python
# Tool definitions in OpenAI tool_calls format

BROWSER_TOOLS = [
    {
        "type": "function",
        "function": {
            "name": "navigate",
            "description": "Открыть URL в браузере. Возвращает сводку видимых элементов (заголовки, ссылки, кнопки, поля ввода). Используй этот инструмент ПЕРВЫМ для каждого нового сайта.",
            "parameters": {
                "type": "object",
                "properties": {
                    "url": {"type": "string", "description": "Полный URL страницы (https://...)"}
                },
                "required": ["url"]
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "snapshot",
            "description": "DOM сводка текущей страницы: до 50 основных элементов (заголовки, ссылки, кнопки, поля ввода). Каждый элемент имеет ref — используй его для click() и type_text().",
            "parameters": {"type": "object", "properties": {}}
        }
    },
    {
        "type": "function",
        "function": {
            "name": "click",
            "description": "Кликнуть по элементу на странице. Возвращает 'ok' или 'error: ...'. ref — из snapshot() или query_dom(). Между кликами есть человеческая задержка.",
            "parameters": {
                "type": "object",
                "properties": {
                    "element": {"type": "string", "description": "ref элемента из snapshot"}
                },
                "required": ["element"]
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "type_text",
            "description": "Ввести текст в поле ввода. Очищает поле перед вводом. Имитирует человеческий ввод (задержка между символами). После type_text обычно нужен press_key('Enter').",
            "parameters": {
                "type": "object",
                "properties": {
                    "element": {"type": "string", "description": "ref элемента из snapshot"},
                    "text": {"type": "string", "description": "Текст для ввода"}
                },
                "required": ["element", "text"]
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "press_key",
            "description": "Нажать клавишу на клавиатуре. Основные: Enter — для отправки формы поиска, Escape — для закрытия модальных окон.",
            "parameters": {
                "type": "object",
                "properties": {
                    "key": {
                        "type": "string",
                        "enum": ["Enter", "Escape", "ArrowDown", "ArrowUp", "Tab", "Backspace"],
                        "description": "Клавиша"
                    }
                },
                "required": ["key"]
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "wait",
            "description": "Подождать указанное количество миллисекунд и вернуть DOM сводку. Используй для ожидания загрузки страницы, JS-рендера, анимаций.",
            "parameters": {
                "type": "object",
                "properties": {
                    "ms": {"type": "integer", "description": "Миллисекунды (100-15000)", "minimum": 100, "maximum": 15000}
                },
                "required": ["ms"]
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "query_dom",
            "description": "Поиск элементов по CSS селектору или тексту. Используй когда snapshot() не показывает нужный элемент, или когда знаешь что ищешь (например, query_dom('.price') для поиска цены).",
            "parameters": {
                "type": "object",
                "properties": {
                    "expression": {"type": "string", "description": "CSS селектор (например, '.price', '#search', 'a[href*=\"product\"]') или текст для поиска на странице"}
                },
                "required": ["expression"]
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "extract_text",
            "description": "Извлечь видимый текст со страницы. Если selector не указан — весь текст страницы (макс 3000 символов). Если указан CSS селектор — текст первого подходящего элемента. Используй для извлечения цены, названия товара.",
            "parameters": {
                "type": "object",
                "properties": {
                    "selector": {"type": "string", "description": "CSS селектор (опционально). Примеры: '.price', '.product-name', 'h1'"}
                }
            }
        }
    },
]

GRAPH_TOOLS = [
    {
        "type": "function",
        "function": {
            "name": "get_approaches",
            "description": "Получить успешные подходы для типа товара. Возвращает: шаги, селекторы, кол-во успехов. Используй ПЕРЕД началом поиска на сайте, чтобы использовать предыдущий опыт.",
            "parameters": {
                "type": "object",
                "properties": {
                    "product_type": {"type": "string", "description": "Тип товара (например: 'ups', 'cable_vvg', 'vfd'). Определи из спецификации."},
                    "site": {"type": "string", "description": "Домен сайта (опционально, без https://). Если указать — вернёт подходы только для этого сайта."}
                },
                "required": ["product_type"]
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "save_approach",
            "description": "Сохранить успешный подход в граф знаний. Вызывай ПОСЛЕ того, как цена найдена и верифицирована. Шаги — массив объектов с действиями.",
            "parameters": {
                "type": "object",
                "properties": {
                    "product_type": {"type": "string", "description": "Тип товара"},
                    "site": {"type": "string", "description": "Домен сайта"},
                    "steps": {
                        "type": "array",
                        "description": "Массив шагов. Каждый шаг: {action, target, target_type, value?, url?}",
                        "items": {"type": "object"}
                    },
                    "selectors": {"type": "string", "description": "JSON: использованные селекторы с fallback'ами"},
                    "method": {"type": "string", "description": "Метод: search_then_navigate, direct_url, catalog_browse"},
                    "search_query": {"type": "string", "description": "Что вводили в поиск (опционально)"},
                    "notes": {"type": "string", "description": "Примечания (опционально)"}
                },
                "required": ["product_type", "site", "steps"]
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "get_confirmed_prices",
            "description": "Получить подтверждённые цены для похожих спецификаций. Возвращает примеры: {spec_text → цена, сайт, уверенность}. Используй как подсказку — если есть похожая спецификация, цена может быть сопоставима.",
            "parameters": {
                "type": "object",
                "properties": {
                    "spec_text": {"type": "string", "description": "Текст спецификации для поиска похожих"}
                },
                "required": ["spec_text"]
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "save_confirmed_price",
            "description": "Записать подтверждённую цену в граф. Вызывай когда уверен в цене (confidence > 0.8). При confidence < 0.6 не вызывай.",
            "parameters": {
                "type": "object",
                "properties": {
                    "spec_text": {"type": "string", "description": "Исходный текст спецификации"},
                    "product_type": {"type": "string", "description": "Тип товара"},
                    "site": {"type": "string", "description": "Домен сайта"},
                    "price": {"type": "number", "description": "Цена в рублях (число)"},
                    "url": {"type": "string", "description": "Ссылка на страницу с ценой"},
                    "confidence": {"type": "number", "description": "Уверенность 0.0-1.0"},
                    "reason": {"type": "string", "description": "Почему цена верна (опционально)"}
                },
                "required": ["spec_text", "product_type", "site", "price", "url", "confidence"]
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "search_sites",
            "description": "Получить список сайтов для типа товара. Возвращает: домены из YAML + из графа (включая auto-discovered). Если YAML пуст — граф может уже знать сайты.",
            "parameters": {
                "type": "object",
                "properties": {
                    "product_type": {"type": "string", "description": "Тип товара"}
                },
                "required": ["product_type"]
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "save_discovered_site",
            "description": "Сохранить новый сайт, найденный через поиск (Yandex, Google). Вызывай когда нашёл цену на сайте, которого нет в известных.",
            "parameters": {
                "type": "object",
                "properties": {
                    "domain": {"type": "string", "description": "Домен сайта (без https://)"},
                    "name": {"type": "string", "description": "Название сайта"},
                    "product_type": {"type": "string", "description": "Тип товара"},
                    "approach_steps": {
                        "type": "array",
                        "description": "Шаги, которыми был найден товар на этом сайте",
                        "items": {"type": "object"}
                    }
                },
                "required": ["domain", "name", "product_type", "approach_steps"]
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "get_hints",
            "description": "Получить подсказки для типа товара. Подсказки содержат информацию о том, КАК искать цены на конкретных сайтах: селекторы, методы поиска, особенности. Вызови когда не уверен, как работать на незнакомом сайте.",
            "parameters": {
                "type": "object",
                "properties": {
                    "product_type": {"type": "string", "description": "Тип товара (обязательно)"}
                },
                "required": ["product_type"]
            }
        }
    },
]

ALL_TOOLS = BROWSER_TOOLS + GRAPH_TOOLS

## 7b. MemoryManager — _classify_intent (v31.1)

Добавлен статический метод, определяющий **намерение** шага по action + target + text:

| Action | Target | Intent |
|---|---|---|
| click | search, find, поиск | click_search_button |
| click | cart, card, product, товар | open_product_card |
| type_text | (text length > 3) | type_search_query |
| type_text | (text length ≤ 3) | type_text |
| press_key(Enter) | — | submit_search |
| press_key(Escape) | — | close_modal |
| extract_text | — | extract_price_content |
| query_dom(.price) | — | find_price_element |
| snapshot | — | observe_page |

Pattern JSON теперь включает `intent`:
```json
{"action": "click", "intent": "click_search_button", "configurable": false}
```

## 7c. format_steps — emoji-отображение подходов (v31.1)

`format_steps()` преобразует concrete steps в строку с эмодзи-префиксами:
```
🔍 click_search_button → ⌨️ type_search_query → ↵ submit_search → 📦 open_product_card → 💰 extract_price_content
```

## 7d. _apply_approach — подстановка param_slots (v31.1)

`_apply_approach(approach, spec_text)` заменяет `{product_name}` в concrete_steps на актуальный spec_text. Применяется в `_build_context()` перед показом LLM.

## 7e. Отрицательная обратная связь (v31.1)

- `record_failure(approach_id)` — зовётся при force switch / MAX_ROUNDS
- 3 consecutive failures → cooldown 24h
- 10 consecutive failures → deprecate
- `record_success(approach_id)` — зовётся при успешном нахождении цены, сбрасывает счётчик
- Фильтр загрузки: `WHERE is_deprecated = 0 AND (cooldown_until IS NULL OR cooldown_until < datetime('now'))`
```

### 8.3 System Prompt

```python
SYSTEM_PROMPT = """Ты — опытный пользователь с доступом к браузеру и базе знаний.

База знаний (граф):
- get_approaches: готовые подходы для (тип_товара, сайт) — следуй им если есть
- search_sites: известные сайты для типа товара
- get_confirmed_prices: ранее найденные цены
- get_hints: подсказки по работе на сайтах

Правила:
1. Сначала проверь get_approaches. Если есть подход — повтори его.
2. Работай с ОДНИМ сайтом за раз. НЕ переключайся между сайтами без причины.
3. browser_snapshot даёт accessibility-tree. Если цены не видны — используй browser_evaluate с JS (querySelectorAll) для прямого извлечения данных из DOM.
4. После поиска на сайте: кликни на карточку товара → откроется страница с ценой. Если цены нет в карточке — ищи на странице через browser_evaluate.
5. Если точного совпадения нет — сохрани лучший найденный аналог (best match). Укажи в reason расхождение в названии. Лучше сохранить похожий товар, чем не сохранить ничего.
6. После нахождения цены: save_confirmed_price + save_approach.
7. Если цена не найдена — верни null, не выдумывай.
8. Если get_confirmed_prices вернул цену с confidence >= 0.9 — используй её как финальную, НЕ проверяй в браузере. Сразу вызови save_confirmed_price.
9. Если ты сделал >10 шагов на одном сайте без результата — принудительно переключись на другой сайт из списка.
10. Если не знаешь, как работать на сайте — вызови get_hints. В хинтах может быть написано, где искать цену, какие селекторы использовать.

Ограничение — 40 шагов на один товар. У тебя полная свобода действий. Кратко поясняй свои намерения перед каждым действием."""
```

### 8.4 agent_loop.py Implementation

```python
import asyncio
import logging
import json
import re
from datetime import datetime

from src.llm_client import LLMClient
from src.tool_parser import parse_tool_calls, parse_final_response
from src.mcp_bridge import MCPBridge
from src.graph_engine import GraphEngine
from src.memory_manager import MemoryManager
from src.validator import validate_result

logger = logging.getLogger("pricer.agent")

# Constants
MAX_ROUNDS = 40  # max total rounds per row
MAX_ROUNDS_PER_SITE = 15  # force switch after 15 rounds on one site
BROWSER_TOOL_NAMES = {"navigate", "snapshot", "click", "type_text", 
                       "press_key", "wait", "query_dom", "extract_text", "get_page_info"}
GRAPH_TOOL_NAMES = {"get_approaches", "save_approach", "get_confirmed_prices",
                     "save_confirmed_price", "search_sites", "save_discovered_site"}

async def process_row(
    spec_text: str,
    llm_client: LLMClient,
    mcp_bridge: MCPBridge,
    graph_engine: GraphEngine,
    memory_manager: MemoryManager,
) -> dict:
    """Process a single spec row. Returns result dict."""
    start_time = datetime.now()
    
    # 1. Determine product type
    product_type = graph_engine.classify_product_type(spec_text)
    
    # 2. Gather context
    approaches = memory_manager.get_all_approaches(product_type) if product_type != "unknown" else []
    confirmed_prices = memory_manager.get_relevant_prices(spec_text)
    sites = memory_manager.get_sites(product_type)
    hints = memory_manager.get_hints(product_type)
    
    # 3. Build YAML context for product type
    yaml_sites_text = _get_yaml_sites_text(product_type)
    
    # 4. Build context block for LLM
    context = _build_context(spec_text, product_type, approaches, confirmed_prices, sites, hints, yaml_sites_text)
    
    # 5. Initial LLM call
    messages = [
        {"role": "system", "content": SYSTEM_PROMPT},
        {"role": "user", "content": context},
    ]
    
    response = await llm_client.chat(messages, ALL_TOOLS)
    if "error" in response:
        return _error_result(spec_text, f"LLM error: {response['error']}")
    
    # 6. Tool loop (up to MAX_ROUNDS)
    rounds = 0
    final_result = None
    
    while rounds < MAX_ROUNDS:
        rounds += 1
        
        tool_calls = parse_tool_calls(response)
        final_attempt = parse_final_response(response)
        
        # If LLM provided a final answer with a code, use it
        if final_attempt.get("selected_code") or final_attempt.get("price") is not None:
            final_result = final_attempt
            break
        
        if not tool_calls:
            # LLM didn't call any tools and didn't provide an answer
            messages.append(_force_json_message())
            response = await llm_client.chat(messages, ALL_TOOLS)
            if "error" in response:
                return _error_result(spec_text, "LLM did not respond")
            continue
        
        # Execute tools
        messages.append(response.get("choices", [{}])[0].get("message", {}))
        
        for tc in tool_calls:
            tool_name = tc.get("name", "")
            tool_args = tc.get("arguments", {})
            
            if tool_name in BROWSER_TOOL_NAMES:
                result = await mcp_bridge.call_tool(tool_name, tool_args)
            elif tool_name in GRAPH_TOOL_NAMES:
                result = _execute_graph_tool(tool_name, tool_args, graph_engine, memory_manager)
            else:
                result = f"error: unknown tool {tool_name}"
            
            messages.append({
                "role": "tool",
                "tool_call_id": tc.get("id", ""),
                "content": str(result)[:3000],  # Truncate long results
            })
        
        # Add result extraction prompt
        messages.append({
            "role": "user",
            "content": (
                "На основе полученных данных верни JSON с результатом поиска цены.\n"
                "Формат: {\"price\": число, \"confidence\": 0.0-1.0, "
                "\"url\": \"ссылка на страницу с ценой\", "
                "\"site\": \"домен сайта\", "
                "\"reason\": \"обоснование\", "
                "\"requires_review\": true/false}\n"
                "Если цена не найдена: {\"price\": null, \"confidence\": 0.0, "
                "\"reason\": \"причина\", \"requires_review\": true}"
            )
        })
        
        response = await llm_client.chat(messages, ALL_TOOLS)
        if "error" in response:
            return _error_result(spec_text, f"LLM error: {response['error']}")
    
    # 7. Parse final result
    if final_result is None:
        final_result = parse_final_response(response)
    
    # 8. Validate
    result = validate_result(final_result, spec_text)
    
    # 9. Save to graph if high confidence
    if result.get("confidence", 0) >= 0.8 and result.get("price") is not None:
        memory_manager.save_price(
            spec_text=spec_text,
            product_type=product_type,
            site=result.get("site", ""),
            price=result["price"],
            url=result.get("url", ""),
            confidence=result["confidence"],
            reason=result.get("reason", ""),
        )
    
    # 10. Log and return
    elapsed = (datetime.now() - start_time).total_seconds()
    logger.info(f"Row done: price={result.get('price')} conf={result.get('confidence', 0):.2f} in {elapsed:.1f}s")
    
    return {
        "spec_text": spec_text,
        "product_type": product_type,
        **result,
        "elapsed": elapsed,
    }


def _build_context(spec_text, product_type, approaches, confirmed_prices, sites, hints, yaml_sites_text):
    """Build initial context block for the LLM."""
    parts = [f"Спецификация: {spec_text}"]
    
    if product_type != "unknown":
        parts.append(f"\nТип товара: {product_type}")
    
    if yaml_sites_text:
        parts.append(f"\nИзвестные сайты для этого типа товара:\n{yaml_sites_text}")
    
    if approaches:
        parts.append("\nУспешные подходы из графа знаний:")
        for a in approaches[:3]:
            s = a.get("site_id", "")
            c = a.get("success_count", 0)
            ls = a.get("last_success_date", "")[:10] if a.get("last_success_date") else "?"
            parts.append(f"  • {s}: успехов={c}, последний={ls}")
            if a.get("method"):
                parts.append(f"    метод: {a['method']}")
            if a.get("search_query"):
                parts.append(f"    поисковой запрос: {a['search_query']}")
    
    if confirmed_prices:
        parts.append("\nПохожие подтверждённые цены (как пример):")
        for p in confirmed_prices[:3]:
            parts.append(f"  • {p.get('spec_text', '')[:60]} → {p.get('price', '?')} ₽ на {p.get('site_id', '?')} (уверенность: {p.get('confidence', 0):.0%})")
    
    if hints:
        parts.append("\nПодсказки:")
        for h in hints[:3]:
            parts.append(f"  • {h.get('hint_text', '')}")
    
    if sites:
        parts.append(f"\nДоступные сайты: {', '.join(s['id'] for s in sites[:5])}")
    
    return "\n".join(parts)


def _execute_graph_tool(name: str, args: dict, engine: GraphEngine, mm: MemoryManager) -> str:
    """Execute a graph tool and return result string."""
    try:
        if name == "get_approaches":
            pt = args.get("product_type", "")
            site = args.get("site")
            approaches = mm.get_all_approaches(pt) if not site else mm.get_site_approaches(pt, site)
            if not approaches:
                return "Нет сохранённых подходов"
            result_parts = [f"Найдено подходов: {len(approaches)}"]
            for a in approaches[:5]:
                pattern = a.get("pattern", [])
                pattern_str = " → ".join(s.get("action", "?") for s in pattern) if pattern else "?"
                result_parts.append(
                    f"  {a.get('site_id', '?')}: {pattern_str} "
                    f"(успехов: {a.get('success_count', 0)}, "
                    f"неудач подряд: {a.get('consecutive_failures', 0)}, "
                    f"метод: {a.get('method', '?')})"
                )
                if a.get("selectors_cache"):
                    result_parts.append(f"    селекторы: {str(a['selectors_cache'])[:200]}")
            return "\n".join(result_parts)
        
        elif name == "save_approach":
            aid = mm.save_approach(
                product_type=args.get("product_type", ""),
                site=args.get("site", ""),
                concrete_steps=args.get("steps", []),
                selectors_cache=json.loads(args.get("selectors", "{}")),
                method=args.get("method", ""),
                search_query=args.get("search_query", ""),
                notes=args.get("notes", ""),
            )
            return f"Подход сохранён (ID: {aid})"
        
        elif name == "get_confirmed_prices":
            prices = mm.get_relevant_prices(args.get("spec_text", ""))
            if not prices:
                return "Нет подтверждённых цен для похожих спецификаций"
            result_parts = [f"Найдено похожих цен: {len(prices)}"]
            for p in prices[:5]:
                result_parts.append(f"  • {p.get('spec_text', '')[:60]} → {p.get('price', '?')} ₽ (уверенность: {p.get('confidence', 0):.0%})")
            return "\n".join(result_parts)
        
        elif name == "save_confirmed_price":
            pid = mm.save_price(
                spec_text=args.get("spec_text", ""),
                product_type=args.get("product_type", ""),
                site=args.get("site", ""),
                price=args.get("price", 0),
                url=args.get("url", ""),
                confidence=args.get("confidence", 0.95),
                reason=args.get("reason", ""),
            )
            return f"Цена сохранена (ID: {pid})" if pid else "Цена не сохранена (confidence < 0.6)"
        
        elif name == "search_sites":
            pt = args.get("product_type", "")
            sites = mm.get_sites(pt)
            if not sites:
                return f"Нет известных сайтов для типа товара «{pt}»"
            return f"Сайты для «{pt}»: {', '.join(s['id'] for s in sites[:10])}"
        
        elif name == "save_discovered_site":
            domain = args.get("domain", "")
            mm.add_site(domain, args.get("name", domain), args.get("product_type", ""))
            # Also save the approach for this new site
            if args.get("approach_steps"):
                mm.save_approach(
                    product_type=args.get("product_type", ""),
                    site=domain,
                    concrete_steps=args["approach_steps"],
                    method="auto_discovered",
                )
            return f"Новый сайт сохранён: {domain}"
        
        return f"error: unknown graph tool: {name}"
    
    except Exception as e:
        logger.exception(f"Graph tool {name} failed")
        return f"error: {e}"


def _force_json_message() -> dict:
    return {
        "role": "user",
        "content": (
            "Верни JSON с результатом поиска цены.\n"
            '{"price": число | null, "confidence": 0.0-1.0, '
            '"url": "строка", "site": "домен", '
            '"reason": "обоснование", "requires_review": bool}'
        ),
    }


def _error_result(spec_text: str, error: str) -> dict:
    logger.error(f"Row failed: {spec_text[:60]} — {error}")
    return {
        "spec_text": spec_text,
        "price": None,
        "confidence": 0.0,
        "reason": error,
        "requires_review": True,
        "error": error,
    }
```

---

## 9. llm_client.py

### 9.1 Overview

Async HTTP client for LM Studio (OpenAI-compatible API). Lightweight, single-provider (only LM Studio for now).

### 9.2 Implementation

```python
import httpx
import json
import asyncio
import logging
from typing import Any

logger = logging.getLogger("pricer.llm")

class LLMClient:
    def __init__(self, base_url: str = "http://localhost:1234/v1", 
                 timeout: float = 150.0, model: str = ""):
        self.base_url = base_url
        self.timeout = timeout
        self.model = model  # auto-detected if empty
        self._client: httpx.AsyncClient | None = None
        self._detected = False
    
    async def __aenter__(self):
        self._client = httpx.AsyncClient(
            timeout=httpx.Timeout(self.timeout, connect=10.0),
            limits=httpx.Limits(max_connections=3),
        )
        return self
    
    async def __aexit__(self, *args):
        if self._client:
            await self._client.aclose()
            self._client = None
    
    async def detect_model(self) -> str | None:
        """Auto-detect model from LM Studio /v1/models."""
        if not self._client:
            return None
        try:
            r = await self._client.get(f"{self.base_url}/models", timeout=5.0)
            if r.status_code == 200:
                data = r.json()
                models = data.get("data", [])
                if models:
                    model_id = models[0].get("id", "")
                    self.model = model_id
                    self._detected = True
                    logger.info(f"LM Studio model detected: {model_id}")
                    return model_id
        except (httpx.TimeoutException, httpx.ConnectError, json.JSONDecodeError):
            logger.warning("LM Studio not detected at %s", self.base_url)
        return None
    
    async def chat(self, messages: list[dict], 
                    tools: list[dict] | None = None) -> dict[str, Any]:
        """Send chat completion request to LM Studio."""
        if not self._client:
            return {"error": "client not initialized"}
        
        body = {
            "model": self.model or "qwen2.5",
            "messages": messages,
            "max_tokens": 4096,
            "temperature": 0.3,  # Low temperature for deterministic tool calls
        }
        if tools:
            body["tools"] = tools
        
        try:
            response = await self._client.post(
                f"{self.base_url}/chat/completions",
                json=body,
                timeout=self.timeout,
            )
            response.raise_for_status()
            result = response.json()
            logger.debug(f"LLM response: {len(str(result))} chars")
            return result
        
        except httpx.TimeoutException:
            logger.error(f"LLM timeout ({self.timeout}s)")
            return {"error": "timeout"}
        except httpx.HTTPStatusError as e:
            logger.error(f"LLM HTTP {e.response.status_code}")
            return {"error": f"http_{e.response.status_code}"}
        except httpx.ConnectError:
            logger.error("LLM connection refused")
            return {"error": "connection_refused"}
        except json.JSONDecodeError:
            logger.error("LLM invalid JSON response")
            return {"error": "invalid_response"}
```

---

## 10. tool_parser.py

### 10.1 Overview

Parses LLM responses — extracts tool calls and final JSON responses. Pattern from Norm_graph's `tool_parser.py`.

### 10.2 Implementation

```python
import json
import logging

logger = logging.getLogger("pricer.tools")

def parse_tool_calls(response: dict) -> list[dict]:
    """Extract tool_calls from LLM response. Returns list of {name, arguments, id}."""
    choice = response.get("choices", [{}])[0]
    message = choice.get("message", {})
    tool_calls = message.get("tool_calls", [])
    
    if not tool_calls:
        return []
    
    parsed = []
    for tc in tool_calls:
        func = tc.get("function", {})
        try:
            args = json.loads(func.get("arguments", "{}"))
        except json.JSONDecodeError:
            logger.warning(f"Failed to parse tool arguments: {func.get('arguments', '')[:200]}")
            args = {}
        parsed.append({
            "name": func.get("name", ""),
            "arguments": args,
            "id": tc.get("id", ""),
        })
    
    return parsed


def _extract_json(text: str) -> dict | None:
    """Extract first JSON object from text using brace-depth parsing."""
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
    """Parse the final LLM response. Returns {price, confidence, url, site, reason, requires_review}."""
    choice = response.get("choices", [{}])[0]
    message = choice.get("message", {})
    content = message.get("content", "")
    
    # Try to extract JSON from content
    result = _extract_json(content) if content else None
    
    if result:
        return {
            "price": result.get("price"),
            "confidence": float(result.get("confidence", 0.5)),
            "url": result.get("url", ""),
            "site": result.get("site", ""),
            "reason": result.get("reason", ""),
            "requires_review": result.get("requires_review", True),
            "alternative_sites": result.get("alternative_sites", []),
        }
    
    # Fallback: no JSON found
    return {
        "price": None,
        "confidence": 0.0,
        "url": "",
        "site": "",
        "reason": content[:500] if content else "Empty response",
        "requires_review": True,
    }
```

---

## 11. validator.py

### 11.1 Overview

Post-validation of LLM results: confidence scoring, anomaly detection, formatting.

### 11.2 Implementation

```python
import re
import logging

logger = logging.getLogger("pricer.validator")


# Confidence rubric mapping
CONFIDENCE_RUBRIC = {
    "product_page": 0.95,   # Price on product detail page
    "catalog_page": 0.85,   # Price on category/search results page
    "yandex_result": 0.70,  # Price found through Yandex fallback
    "vision_extract": 0.50, # Price extracted from screenshot (Vision)
    "estimate": 0.30,       # LLM estimated from similar products
    "not_found": 0.0,       # No price found
}


def validate_result(result: dict, spec_text: str = "") -> dict:
    """Post-validate LLM result. Returns corrected result."""
    
    # Ensure all fields exist
    result.setdefault("price", None)
    result.setdefault("confidence", 0.0)
    result.setdefault("url", "")
    result.setdefault("site", "")
    result.setdefault("reason", "")
    result.setdefault("requires_review", True)
    
    price = result.get("price")
    confidence = result.get("confidence", 0.0)
    
    # Case 1: No price found
    if price is None:
        result["confidence"] = 0.0
        result["requires_review"] = True
        return result
    
    # Case 2: Price should be positive number
    try:
        price = float(price)
        if price <= 0:
            result["price"] = None
            result["confidence"] = 0.0
            result["reason"] += " (некорректная цена: отрицательная или ноль)"
            result["requires_review"] = True
            return result
    except (ValueError, TypeError):
        result["price"] = None
        result["confidence"] = 0.0
        result["reason"] += " (цена не является числом)"
        result["requires_review"] = True
        return result
    
    # Case 3: Confidence should be in range
    confidence = max(0.0, min(1.0, confidence))
    result["confidence"] = confidence
    
    # Case 4: Low confidence → review
    if confidence < 0.6:
        result["requires_review"] = True
    
    # Case 5: High confidence → maybe auto
    if confidence >= 0.8 and result.get("url") and result.get("site"):
        result["requires_review"] = False
    
    # Case 6: Anomalous price (> 10M RUB) → needs review
    if price > 10_000_000:
        result["confidence"] *= 0.8
        result["requires_review"] = True
        result["reason"] += " (аномально высокая цена)"
    
    # Case 7: Suspiciously low price (< 1 RUB)
    if price < 1.0:
        result["confidence"] *= 0.5
        result["requires_review"] = True
        result["reason"] += " (подозрительно низкая цена)"
    
    # Cap confidence
    result["confidence"] = round(min(result["confidence"], 1.0), 2)
    
    return result


def format_price_for_display(price: float) -> str:
    """Format price for Excel output."""
    if price is None:
        return ""
    if price >= 1000:
        return f"{price:,.2f}".replace(",", " ")
    return f"{price:.2f}"
```

---

## 12. GUI

### 12.1 gui/main_window.py

Refactored from existing `main.py`. Key components:

```python
# MainWindow (PySide6)
class MainWindow(QMainWindow):
    def __init__(self):
        super().__init__()
        self.setWindowTitle("Pricer Vision v31.0 — MCP Agent")
        
        # State
        self.engine: GraphEngine | None = None
        self.mm: MemoryManager | None = None
        self.llm_client: LLMClient | None = None
        self._runner: MCPAgentRunner | None = None
        
        # Build UI (same as v30.1 but with Graph Assistant tab)
        self._init_toolbar()
        self._init_central()
        self._init_statusbar()
        
        # Background init
        QTimer.singleShot(100, self._init_background)
    
    def _init_background(self):
        """Async init: build graph, detect LLM."""
        # Worker thread
        class InitWorker(QThread):
            done = pyqtSignal(object, object)
            def run(self):
                engine = GraphEngine(DB_PATH)
                engine.build()
                # Detect LLM
                loop = asyncio.new_event_loop()
                asyncio.set_event_loop(loop)
                client = LLMClient()
                loop.run_until_complete(client.__aenter__())
                model = loop.run_until_complete(client.detect_model())
                self.done.emit(engine, client)
        
        self._init_worker = InitWorker()
        self._init_worker.done.connect(self._on_init_done)
        self._init_worker.start()
    
    def _on_init_done(self, engine, llm_client):
        self.engine = engine
        self.mm = MemoryManager(engine)
        self.llm_client = llm_client
        self.graph_status.setText(f"Граф: {len(engine._all_products)} товаров, {len(engine._all_sites)} сайтов")
        self.llm_status.setText(f"LLM: {llm_client.model or 'подключён'}")
        self.assistant_widget.set_engine(engine)
    
    def _start_processing(self):
        """Start batch processing from Excel file."""
        # ... file dialog, load specs, start MCPAgentRunner ...
        self._runner = MCPAgentRunner(
            specs=specs,
            llm_client=self.llm_client,
            db_path=DB_PATH,
        )
        self._runner.status_signal.connect(self._on_status)
        self._runner.row_done_signal.connect(self._on_row)
        self._runner.done_signal.connect(self._on_done)
        self._runner.error_signal.connect(self._on_error)
        self._runner.start()
```

### 12.2 gui/graph_assistant.py

11-tool panel with global product type combo. Pattern: Norm_graph `AssistantChat` → extended for full CRUD.

| # | Tab | Widget | Function |
|---|------|--------|----------|
| 0 | Справка | `HelpPage` | Full documentation: all pages, examples, best practices, data flow, FAQ |
| 1 | Контекст графа | `ContextPage` | table: type name (rus), sites, approaches, prices count. NoEditTriggers |
| 2 | Поиск подходов | `SearchPage` | combo + site filter (currentData, not text), delete/deprecate approaches, cursor highlight |
| 3 | Сайты | `SitePage` | manage site bindings per product type, priority (primary/secondary/all). NoEditTriggers |
| 4 | Подходы | `ApproachPage` | all approaches table, filter by product type + site, show deprecated, view steps, delete/deprecate. NoEditTriggers |
| 5 | Цены | `PricePage` | all confirmed prices table, search, edit price, delete. NoEditTriggers |
| 6 | Типы товаров | `ProductTypePage` | CRUD table, rename, delete (cascade), reload YAML seed. Editable (rename) |
| 7 | Подсказки | `HintPage` | combo + priority + add/show all/delete by ID, cursor highlight |
| 8 | Коррекция цен | `CorrectionPage` | combo (currentData) for site, combo for product type |
| 9 | Обучение | `StudyPage` | URL + spec + type → StudyRunner. save_concept_edge via mm, rebuild() |
| 10 | Статистика | `StatsPage` | graph stats + recent approaches |

**Key fixes applied in v31.2:**
- All `site_combo` fields use `currentData() or currentText()` (not `currentText()` alone) — fixes site ID mismatch for `get_site_approaches()` and `save_price()`
- `engine._all_products` replaced with `engine.get_all_products()` everywhere
- Concept saving uses `mm.save_concept_edge()` instead of raw SQL on `engine._conn`
- Rebuild uses `engine.rebuild()` instead of `_built = False; build()`
- Product type filter added to ApproachPage

**Button styling (7 variants, no hardcoded colors):**
- `#primary` — accent accent-filled (search, save, start study)
- `#success` — green-filled (save selected approaches)
- `#danger` — ghost red border (delete buttons, stop study)
- `#warning` — ghost amber border (deprecate, reload YAML)
- `#ghost` — transparent (graph explorer toolbar)
- `#small-btn` — minimal padding (+ add site)
- default — `bg-surface` + 1px border

All combos display Russian names (not English IDs). Editable combos resolve typed Russian text via `resolve_pt()`.

### 12.3 UI Layout Architecture (v31.3)

Фиксированные размеры для верхних элементов, splitter забирает весь остаток.

| Элемент | Высота | Stretch | Margins | Описание |
|---------|--------|---------|---------|----------|
| `btn_frame` | 38px | 0 | (6, 3, 6, 3) | Toolbar: кнопки + чекбоксы |
| `fb_frame` | 28px | 0 | (6, 2, 6, 2) | Статус: спиннер 16px + label |
| `progress_bar` | 21px | 0 | — | Gradient chunk, border-radius 6px |
| `splitter` | auto | 1 | — | Таблицы + график (единственный растягиваемый) |
| `main_layout` | — | — | (10, 2, 10, 10) | spacing=4 |

**Принцип:** `addWidget(widget, stretch=0)` — фиксированная высота, `addWidget(splitter, stretch=1)` — забирает остаток. Только таблица и график уменьшаются по вертикали.

**Spinner:** 16×16px (main), 24px (study), `spacing=0.5` — точки прежнего размера, расстояния уменьшены.

**Progress bar:** QProgressBar::chunk с gradient (solid → 87% opacity → solid), border с `t["border"]`, inset margin 1px.

```python
class GraphAssistantPanel(QWidget):
    """5-tool panel for graph editing. Patterned after Norm_graph AssistantChat."""
    
    def __init__(self, parent=None):
        super().__init__(parent)
        self.engine: GraphEngine | None = None
        self.mm: MemoryManager | None = None
        
        self._list = QListWidget()
        self._list.setFixedWidth(180)
        self._list.addItems([
            "🔍 Поиск подходов",
            "📋 Контекст подхода", 
            "💡 Подсказки",
            "✅ Коррекции цен",
            "📊 Статистика",
        ])
        
        self._stack = QStackedWidget()
        self._pages = [
            SearchPage(self),
            ContextPage(self),
            HintPage(self),
            CorrectionPage(self),
            StatsPage(self),
        ]
        for p in self._pages:
            self._stack.addWidget(p)
        
        # Layout
        splitter = QSplitter(Qt.Horizontal)
        splitter.addWidget(self._list)
        splitter.addWidget(self._stack)
        layout = QVBoxLayout(self)
        layout.addWidget(splitter)
        
        self._list.currentRowChanged.connect(lambda r: self._stack.setCurrentIndex(r))
        self._list.setCurrentRow(0)
    
    def set_engine(self, engine):
        self.engine = engine
        self.mm = MemoryManager(engine)
        for p in self._pages:
            if hasattr(p, 'refresh'):
                p.refresh()
```

---

## 13. MCPAgentRunner

### 13.1 QThread Wrapper

```python
class MCPAgentRunner(QThread):
    status_signal = pyqtSignal(object)
    row_done_signal = pyqtSignal(int, object)
    done_signal = pyqtSignal(bool, object)
    error_signal = pyqtSignal(str)
    
    def __init__(self, specs: list, llm_client, db_path: str, parent=None):
        super().__init__(parent)
        self.specs = specs
        self.llm_client = llm_client
        self.db_path = db_path
        self._stop_event = threading.Event()
    
    def run(self):
        """Run in background thread with asyncio event loop."""
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
        try:
            loop.run_until_complete(self._run_async())
        finally:
            loop.close()
    
    async def _run_async(self):
        self.status_signal.emit("start")
        
        # Init graph engine
        self.status_signal.emit(("progress", 0, len(self.specs), "Загрузка графа..."))
        engine = GraphEngine(self.db_path)
        engine.build()
        mm = MemoryManager(engine)
        
        # Init MCP bridge
        self.status_signal.emit(("progress", 0, len(self.specs), "Запуск браузера..."))
        bridge = MCPBridge()
        if not await bridge.start():
            self.error_signal.emit("Не удалось запустить MCP сервер (pricer-mcp-server)")
            return
        
        # Init LLM client
        self.status_signal.emit(("progress", 0, len(self.specs), "Подключение к LLM..."))
        await self.llm_client.__aenter__()
        
        try:
            results = []
            for i, spec in enumerate(self.specs):
                if self._stop_event.is_set():
                    break
                
                self.status_signal.emit(("progress", i, len(self.specs), 
                    f"Обработка: {spec.text[:50]}..."))
                
                result = await process_row(
                    spec_text=spec.text,
                    llm_client=self.llm_client,
                    mcp_bridge=bridge,
                    graph_engine=engine,
                    memory_manager=mm,
                )
                
                results.append(result)
                self.row_done_signal.emit(i, result)
            
            # Done
            spec_result = {
                "total": len(results),
                "positions": results,
                "found_count": sum(1 for r in results if r.get("price") is not None),
                "review_count": sum(1 for r in results if r.get("requires_review")),
                "error_count": sum(1 for r in results if r.get("error")),
            }
            self.done_signal.emit(True, spec_result)
        
        finally:
            await bridge.stop()
            await self.llm_client.__aexit__(None, None, None)
    
    def stop(self):
        self._stop_event.set()
```

---

## 14. Complete Pipeline Flow

```
USER LOADS EXCEL
  → MainWindow: file dialog → specs list
  → User clicks "Start"
      ↓

MCPAgentRunner.start()
  ├── GraphEngine.build()          (~1-2s: SQLite → in-memory)
  ├── MCPBridge.start()            (~2-3s: subprocess → MCP initialize)
  └── LLMClient.__aenter__()       (~0.1s: HTTP client init)
      ↓

For EACH spec row:
  process_row(spec_text, llm, bridge, graph, mm):
    ├── GraphEngine.classify_product_type()   (keyword match)
    ├── MemoryManager.get_approaches()        (check graph memory)
    ├── MemoryManager.get_relevant_prices()   (few-shot examples)
    ├── LLM: initial call with context + tools
    │
    ├── [LOOP] while rounds < 8:
    │   ├── LLM → tool_calls[click, type, navigate, ...]
    │   │   OR → final result JSON
    │   │
    │   ├── BROWSER TOOL: MCPBridge.call_tool()
    │   │   → pricer-mcp-server → nodriver → result
    │   │
    │   └── GRAPH TOOL: GraphEngine local call
    │       → result string
    │
    ├── Validator.validate_result()            (price range, confidence)
    │
    ├── MemoryManager.save_price()             (if confidence ≥ 0.8)
    │
    └── → result dict
        ↓

row_done_signal(i, result)
  └── MainWindow: add row to table + status update
      ↓

ALL ROWS DONE:
  done_signal(True, spec_result)
  └── MainWindow: show summary, enable Excel export
```

---

## 15. Configuration

### 15.1 config/settings.yaml

```yaml
llm:
  provider: lm_studio
  base_url: "http://localhost:1234/v1"
  timeout: 150
  max_tokens: 4096

graph:
  db_path: "data/pricer.db"

paths:
  input: "data/input"
  output: "data/output"
  backup: "data/backup"
  checkpoints: "data/checkpoints"

processing:
  max_rounds_per_row: 8
  max_sites_per_row: 5
```

### 15.2 config/categories_and_sites.yaml

Same as v30.1 (unchanged). Used as seed data. The `category_map` structure with product types and associated sites is loaded at startup into `graph_engine._all_products` and `graph_engine._product_sites`.

```python
def load_yaml_seed(graph_engine: GraphEngine, yaml_path: str):
    """Load YAML seed data into graph engine memory."""
    with open(yaml_path, "r", encoding="utf-8") as f:
        config = yaml.safe_load(f)
    
    category_map = config.get("category_map", {})
    for cat_name, cat_data in category_map.items():
        for subcat_key, subcat_data in cat_data.get("subcategories", {}).items():
            # Create product type ID
            product_type_id = f"{cat_name}_{subcat_key}" if subcat_key else cat_name
            
            # Register in graph (if not exists)
            # ... insert into product_types table
            
            # Register sites
            for site_entry in subcat_data.get("sites", []):
                site_id = site_entry["site"] if isinstance(site_entry, dict) else site_entry
                # ... insert into sites + product_sites
```

---

## 16. / 17. / 18. — Error Handling / Testing / Performance / Migration

(These will be detailed in subsequent sections, focused on the core architecture in this document. Full sections can be expanded if needed.)

---

## 19. Key Design Decisions Summary

| Decision | Rationale |
|---|---|
| **nodriver → MCP server** | Existing anti-detection + MCP protocol compatibility |
| **SQLite + dicts (no NetworkX)** | <10000 entities, direct queries are faster and simpler |
| **Own MCP server (not Playwright)** | Anti-detection control + nodriver integration |
| **Tool loop from Norm_graph** | Proven pattern: LLM → execute → parse → re-prompt |
| **Approach = pattern + concrete + selectors_cache** | Two-level abstraction: semantics + DOM-specific |
| **Temporal data (cooldown, deprecated)** | Prevents reuse of broken approaches |
| **YAML = seed only** | LLM can discover sites outside YAML |
| **No instructions.md / schema.json** | LLM learns from tools and context, not hardcoded prompts |
| **10-tool assistant (study + CRUD)** | Full graph management + forced learning tool |
| **System prompt = tool API docs** | Documents what tools do, not how to sequence them |

---

## 20. Study Runner (Forced Learning)

### 20.1 Purpose

When the main pipeline fails to find a price for a position, the user can manually provide the correct product URL and launch the study runner. Its goal is NOT to find one price — it's to understand the site's structure and create reusable approaches for the entire product type.

### 20.2 Architecture

```
User finds URL → StudyPage (UI) → StudyRunner (QThread)
                                      ├── Own MCPBridge (Playwright)
                                      ├── Own LLMClient (same config, temp=0.5)
                                      ├── Shared GraphEngine (same DB)
                                      └── 30 rounds, custom prompt
                                              │
                                              ├── ask_user tool → UI question → answer → continue
                                              ├── save_approach → collected as draft
                                              ├── save_confirmed_price → saved immediately
                                              └── done → approaches_signal → done_signal

UI: approaches_signal → checkboxes → user selects → save_selected → graph refresh
```

### 20.3 Key Differences from Main Pipeline

| Aspect | Main Pipeline | Study Runner |
|---|---|---|
| Goal | Find price | Understand site, create approaches |
| URL | Agent searches | User provides |
| max rounds | 40 | 30 |
| Temperature | 0.3 (config) | 0.5 (forced) |
| save_approach | Saves directly | Collected as draft, user approves |
| ask_user | Not available | Available |
| Site guides | From `_build_context` | Pulled from all product types |
| Failure context | Not applicable | Passed from main pipeline error |

### 20.4 Prompt

```
Ты — аналитик по настройке поиска цен на сайтах поставщиков.
ПЛАН РАБОТЫ:
1. Открой URL, найди цену, сохрани через save_confirmed_price.
2. Изучи сайт: как работает поиск, каталог, карточки товаров.
3. Попробуй найти товар через поиск на сайте (не по URL).
4. Разработай подходы → вызови save_approach (будет предложен на утверждение).
5. Если нужно уточнить → ask_user(вопрос).
ВАЖНО: подходы должны быть общими для ТИПА товара.
```

### 20.5 Tool Inventory (Study Runner)

| Tool | Description |
|---|---|
| get_approaches | From graph (product_type, site, or all) |
| save_approach | Collected as draft for user approval |
| get_confirmed_prices | Few-shot from graph |
| save_confirmed_price | Saved immediately |
| ask_user | Question → UI response → next round |
| search_sites | From graph |
| save_discovered_site | New site → graph |

### 20.6 Files

- `src/study_runner.py` — `StudyRunner(QThread)`, `_clean_snapshot()`, `_proposal_key()` for dedup
- `gui/graph_assistant.py` — `StudyPage` (URL/spec/type inputs, conversation log, Q&A frame, approach checkboxes, `_save_selected_approaches()`, graph refresh)
- `main.py` — 📖 button in toolbar, 📖 per results row, `_switch_to_assistant()`, `_open_study()`, `_open_study_tool()`

