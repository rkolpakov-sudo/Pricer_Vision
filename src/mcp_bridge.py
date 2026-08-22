import asyncio
import os
import sys
import logging
from pathlib import Path
from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client

from src.resilience import mcp_circuit
from src.config_loader import get_mcp_config, load_settings

logger = logging.getLogger("pricer.bridge")

_HASH_REF_RE = __import__("re").compile(r"^[ef]\d+$|^f\d+e\d+$")

_BACKENDS = ("camoufox", "playwright", "nodriver")
_DEFAULT_BACKENDS = ("camoufox", "playwright", "nodriver")


def resolve_backends(cfg: dict | None = None) -> list[str]:
    """Ordered backend chain from settings.

    ``browser.backend`` is the preferred/default backend (always first);
    ``browser.backends`` is the full failover order. Unknown names are dropped
    and the default set is always completed, so the result is never empty.
    """
    cfg = cfg if cfg is not None else load_settings()
    browser = cfg.get("browser", {}) or {}
    primary = browser.get("backend") or "camoufox"
    chain = browser.get("backends") or list(_DEFAULT_BACKENDS)

    out: list[str] = []
    for b in chain:
        if b in _BACKENDS and b not in out:
            out.append(b)
    if primary in _BACKENDS:
        if primary in out:
            out.remove(primary)
        out.insert(0, primary)
    for b in _DEFAULT_BACKENDS:
        if b not in out:
            out.append(b)
    return out


def _is_hash_ref(ref: str) -> bool:
    """Check if ref is an internal accessibility tree hash (e68, f5e17) vs a role locator."""
    return bool(_HASH_REF_RE.match(ref))


def _is_url(text: str) -> bool:
    return isinstance(text, str) and text.lower().startswith(("http://", "https://"))


def _comment_start_outside_strings(line: str) -> int:
    """Index of the first `//` that is NOT inside a string literal, else -1."""
    i, n = 0, len(line)
    while i < n:
        ch = line[i]
        if ch in "'\"`":
            quote = ch
            i += 1
            while i < n:
                if line[i] == "\\":
                    i += 2
                    continue
                if line[i] == quote:
                    break
                i += 1
        elif ch == "/" and i + 1 < n and line[i + 1] == "/":
            return i
        i += 1
    return -1


def _sanitize_js(js: str) -> str:
    """Repairs LLM-generated JS that would otherwise throw `SyntaxError: Unexpected end of input`.

    Small LLMs often emit `return x; // comment }` — the closing brace lands on the
    same line AFTER a `//` comment and gets swallowed, leaving the arrow function open.
    We strip a trailing single-line comment and append any missing closing braces."""
    if not js:
        return js
    s = js.strip()
    lines = s.split("\n")
    ci = _comment_start_outside_strings(lines[-1])
    if ci != -1:
        before = lines[-1][:ci].rstrip()
        if before.endswith(("{", "}", ";", ")")):
            lines[-1] = before
            s = "\n".join(lines).rstrip()
    depth = 0
    in_str = None
    in_line_comment = False
    i, n = 0, len(s)
    while i < n:
        ch = s[i]
        if in_line_comment:
            if ch == "\n":
                in_line_comment = False
        elif in_str:
            if ch == "\\":
                i += 2
                continue
            if ch == in_str:
                in_str = None
        elif ch in "'\"`":
            in_str = ch
        elif ch == "/" and i + 1 < n and s[i + 1] == "/":
            in_line_comment = True
            i += 1
        elif ch == "{":
            depth += 1
        elif ch == "}":
            depth -= 1
        i += 1
    if depth > 0:
        s += "}" * depth
    return s

_STEALTH_JS = str(Path(__file__).resolve().parent.parent / "config" / "stealth.js")
_MCP_CONFIG = str(Path(__file__).resolve().parent.parent / "config" / "playwright-mcp.json")


class _ServerConnection:
    def __init__(self, name: str, command: str, args: list[str]):
        self.name = name
        self.command = command
        self.args = args
        self.stdio_ctx = None
        self.session_ctx = None
        self.session = None
        self.tools = []


class MCPBridge:
    def __init__(self, headless: bool = False, call_timeout: float | None = None,
                 restart_after_timeouts: int | None = None):
        self._servers: list[_ServerConnection] = []
        self._tool_map: dict[str, _ServerConnection] = {}
        self._lock = asyncio.Lock()
        self._stopped = False
        self._headless = headless
        self._call_timeout = call_timeout if call_timeout is not None else get_mcp_config("call_timeout", 60.0)
        self._restart_after_timeouts = (
            restart_after_timeouts if restart_after_timeouts is not None
            else get_mcp_config("restart_after_timeouts", 2)
        )
        self._consecutive_timeouts = 0
        self._backend: str | None = None
        self._backend_override: str | None = None

    async def start(self) -> bool:
        self._stopped = False

        backends = resolve_backends()
        if self._backend_override and self._backend_override in _BACKENDS:
            backends = [self._backend_override,
                        *[b for b in backends if b != self._backend_override]]
        last_err: Exception | None = None
        for backend in backends:
            try:
                if await self._start_one(backend):
                    self._backend = backend
                    self._stopped = False
                    logger.info("MCP bridge ready: backend=%s (%d tools)", backend, len(self._tool_map))
                    return True
            except Exception as e:
                last_err = e
                logger.error("Backend '%s' start raised: %s", backend, e)
            logger.warning("Backend '%s' failed to start — trying next in chain: %s", backend, backends)
            await self.stop()
        if last_err:
            logger.error("All backends failed to start (last error: %s)", last_err)
        return False

    def _build_server(self, backend: str) -> _ServerConnection | None:
        if backend == "playwright":
            pin = (load_settings().get("deps", {}).get("playwright_mcp") or {}).get("version")
            pw_pkg = f"@playwright/mcp@{pin}" if pin else "@playwright/mcp"
            pw_args = [pw_pkg, "--browser", "chrome"]
            if self._headless:
                pw_args.append("--headless")
            if os.path.exists(_STEALTH_JS):
                pw_args.extend(["--init-script", _STEALTH_JS])
            if os.path.exists(_MCP_CONFIG):
                pw_args.extend(["--config", _MCP_CONFIG])
            pw_args.extend(["--viewport-size", "1920x1080"])
            pw_args.extend(["--timeout-action", "10000"])
            pw_args.extend(["--timeout-navigation", "30000"])
            return _ServerConnection("playwright", "npx.cmd", pw_args)
        if backend in ("camoufox", "nodriver"):
            script = str(Path(__file__).resolve().parent.parent / "mcp_servers" / "browser_server.py")
            # Запускать сервер СВОИМ venv проекта, а не интерпретатором, которым запущено
            # приложение (sys.executable может указывать на чужой venv и не содержать
            # camoufox/nodriver/нужной версии mcp).
            own_venv = Path(__file__).resolve().parent.parent / "venv" / "Scripts" / "python.exe"
            interpreter = str(own_venv) if own_venv.exists() else sys.executable
            args = [script, "--backend", backend]
            if self._headless:
                args.append("--headless")
            return _ServerConnection(backend, interpreter, args)
        logger.error("Unknown browser backend: %s", backend)
        return None

    async def _start_one(self, backend: str) -> bool:
        srv = self._build_server(backend)
        if srv is None:
            return False
        logger.info("MCP launch: backend=%s command=%s %s (mode=%s)",
                    backend, srv.command, " ".join(str(a) for a in srv.args[:6]),
                    "headless" if self._headless else "headed")
        for attempt in range(2):
            try:
                params = StdioServerParameters(
                    command=srv.command, args=srv.args,
                    env=os.environ.copy(),
                )
                srv.stdio_ctx = stdio_client(params)
                read, write = await srv.stdio_ctx.__aenter__()
                srv.session_ctx = ClientSession(read, write)
                srv.session = await srv.session_ctx.__aenter__()
                await asyncio.wait_for(srv.session.initialize(), timeout=30.0)
                resp = await srv.session.list_tools()
                srv.tools = resp.tools
                self._servers.append(srv)
                for t in resp.tools:
                    self._tool_map[t.name] = srv
                logger.info("MCP server '%s' started: %d tools", srv.name, len(resp.tools))
                return True
            except Exception as e:
                logger.error("MCP server '%s' start failed (attempt %d): %s", srv.name, attempt + 1, e)
                await asyncio.sleep(2 if attempt == 0 else 0)
            finally:
                if srv not in self._servers:
                    for ctx_attr in ('session_ctx', 'stdio_ctx'):
                        ctx = getattr(srv, ctx_attr, None)
                        if ctx is not None:
                            try:
                                await ctx.__aexit__(None, None, None)
                            except Exception:
                                logger.warning("Failed to cleanup %s on %s", ctx_attr, srv)
                            setattr(srv, ctx_attr, None)
        return False

    async def call_tool(self, tool_name: str, arguments: dict) -> str:
        if self._stopped:
            return "error: bridge stopped"
        srv = self._tool_map.get(tool_name)
        if not srv or not srv.session:
            return f"error: tool '{tool_name}' not found on any server"
        # Circuit Breaker: блокируем вызовы при открытом состоянии (после восстановления — restart)
        if not mcp_circuit.allow_request():
            logger.error("MCP unavailable, restarting...")
            await self.restart()
            return "error: MCP circuit open"
        # Clean empty selector args that cause Playwright parsing errors
        for key in ("target", "element", "ref"):
            if key in arguments and not arguments[key]:
                logger.warning("🔧 Cleaning empty '%s' from %s args", key, tool_name)
                del arguments[key]
        # LLM often passes 'ref' or 'element' instead of 'target' for click/type tools.
        # Only map role-based refs (e.g. 'textbox "Поиск"') — hash refs (e68) are internal
        # accessibility tree identifiers, not CSS selectors.
        if tool_name in ("browser_type", "browser_click", "browser_hover", "browser_fill_form"):
            if "target" not in arguments or not arguments.get("target"):
                ref = arguments.get("ref") or arguments.get("element") or ""
                if ref and not _is_hash_ref(ref):
                    arguments["target"] = ref
        # LLM discovers links via browser_evaluate and then tries to click them by URL.
        # Playwright MCP fails on a URL as a CSS selector — rewrite to navigation.
        if tool_name == "browser_click":
            url_target = arguments.get("target") or arguments.get("ref") or arguments.get("element") or ""
            if _is_url(url_target):
                logger.info("🔗 browser_click target is a URL (%s...) — rewriting to browser_navigate", url_target[:60])
                tool_name = "browser_navigate"
                arguments = {"url": url_target}
        # Small LLMs emit broken JS (trailing `//` swallows closing braces) that
        # Playwright rejects with "SyntaxError: Unexpected end of input" — repair it.
        if tool_name in ("browser_evaluate", "browser_run_code_unsafe"):
            fn = arguments.get("function") or arguments.get("code") or ""
            sanitized = _sanitize_js(fn)
            if sanitized != fn:
                logger.info("🔧 browser_evaluate JS repaired (%d → %d chars)", len(fn), len(sanitized))
                if tool_name == "browser_evaluate":
                    arguments["function"] = sanitized
                else:
                    arguments["code"] = sanitized
        async with self._lock:
            try:
                result = await asyncio.wait_for(
                    srv.session.call_tool(tool_name, arguments),
                    timeout=self._call_timeout,
                )
                self._consecutive_timeouts = 0
                parts = []
                for content in result.content:
                    if hasattr(content, "text"):
                        parts.append(content.text)
                    elif hasattr(content, "data"):
                        parts.append(f"[binary: {len(content.data)} bytes]")
                    elif hasattr(content, "type") and content.type == "resource":
                        parts.append(f"[resource: {getattr(content, 'uri', '?')}]")
                mcp_circuit.record_success()
                return "\n".join(parts)
            except asyncio.TimeoutError:
                mcp_circuit.record_failure()
                self._consecutive_timeouts += 1
                logger.warning("MCP tool '%s' on '%s' timed out after %.0fs (consecutive: %d)",
                               tool_name, srv.name, self._call_timeout, self._consecutive_timeouts)
                if self._consecutive_timeouts >= self._restart_after_timeouts:
                    self._consecutive_timeouts = 0
                    logger.warning("MCP stuck (repeated timeouts) — restarting bridge")
                    await self._restart_safe()
                return f"error: tool call timed out after {self._call_timeout:.0f}s"
            except Exception as e:
                mcp_circuit.record_failure()
                logger.warning("MCP tool '%s' on '%s' failed: %s", tool_name, srv.name, e)
                return f"error: tool call failed: {e}"

    async def list_tools(self) -> list[dict]:
        if self._stopped:
            return []
        all_tools = []
        async with self._lock:
            for srv in self._servers:
                if not srv.session:
                    continue
                try:
                    resp = await srv.session.list_tools()
                    for tool in resp.tools:
                        schema = getattr(tool, "input_schema", None)
                        if schema is None:
                            schema = getattr(tool, "inputSchema", None)
                        all_tools.append({
                            "type": "function",
                            "function": {
                                "name": tool.name,
                                "description": tool.description or "",
                                "parameters": schema if isinstance(schema, dict) else {"type": "object", "properties": {}},
                            }
                        })
                except Exception as e:
                    logger.warning("list_tools on '%s' failed: %s", srv.name, e)
        logger.info("MCP tools (%d): %s", len(all_tools), [t["function"]["name"] for t in all_tools])
        return all_tools

    async def health_check(self) -> bool:
        alive = False
        for srv in self._servers:
            try:
                if srv.session:
                    await srv.session.send_ping()
                    alive = True
            except Exception:
                logger.warning("Health check failed for '%s'", srv.name)
        return alive

    async def restart(self) -> bool:
        logger.info("MCP bridge restarting...")
        await self.stop()
        await asyncio.sleep(1.0)
        return await self.start()

    async def _restart_safe(self) -> None:
        """Рестарт с защитным таймаутом: очистка зависшей сессии не должна блокировать."""
        try:
            await asyncio.wait_for(self.restart(), timeout=20.0)
        except Exception:
            logger.warning("MCP bridge restart timed out — continuing")

    async def set_headless(self, headless: bool) -> bool:
        self._headless = headless
        return await self.restart()

    async def set_backend(self, backend: str) -> bool:
        """Switch browser backend at runtime and restart the bridge."""
        if backend not in _BACKENDS:
            logger.warning("Ignoring unknown backend '%s'", backend)
            return False
        self._backend_override = backend
        return await self.restart()

    async def stop(self):
        self._stopped = True
        for srv in self._servers:
            try:
                if srv.session_ctx:
                    await srv.session_ctx.__aexit__(None, None, None)
            except Exception:
                logger.warning("Failed to cleanup session ctx on %s", srv)
            try:
                if srv.stdio_ctx:
                    await srv.stdio_ctx.__aexit__(None, None, None)
            except Exception:
                logger.warning("Failed to cleanup stdio ctx on %s", srv)
        self._servers.clear()
        self._tool_map.clear()
        logger.info("MCP bridge stopped")
