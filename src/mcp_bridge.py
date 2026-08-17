import asyncio
import os
import sys
import logging
from pathlib import Path
from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client

from src.resilience import mcp_circuit, CircuitState

logger = logging.getLogger("pricer.bridge")

_HASH_REF_RE = __import__("re").compile(r"^[ef]\d+$|^f\d+e\d+$")


def _is_hash_ref(ref: str) -> bool:
    """Check if ref is an internal accessibility tree hash (e68, f5e17) vs a role locator."""
    return bool(_HASH_REF_RE.match(ref))

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
    def __init__(self, headless: bool = False):
        self._servers: list[_ServerConnection] = []
        self._tool_map: dict[str, _ServerConnection] = {}
        self._lock = asyncio.Lock()
        self._stopped = False
        self._headless = headless

    async def start(self) -> bool:
        self._stopped = False

        from src.config_loader import load_settings
        pin = (load_settings(reload=True).get("deps", {}).get("playwright_mcp") or {}).get("version")
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
        pw = _ServerConnection("playwright", "npx.cmd", pw_args)

        for srv in (pw,):
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
                    await asyncio.wait_for(srv.session.initialize(), timeout=15.0)
                    resp = await srv.session.list_tools()
                    srv.tools = resp.tools
                    self._servers.append(srv)
                    for t in resp.tools:
                        self._tool_map[t.name] = srv
                    logger.info("MCP server '%s' started: %d tools", srv.name, len(resp.tools))
                    break
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

        if self._servers:
            logger.info("MCP bridge ready: %d servers, %d total tools",
                        len(self._servers), len(self._tool_map))
            return True
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
        async with self._lock:
            try:
                result = await srv.session.call_tool(tool_name, arguments)
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
                        all_tools.append({
                            "type": "function",
                            "function": {
                                "name": tool.name,
                                "description": tool.description or "",
                                "parameters": tool.inputSchema if isinstance(tool.inputSchema, dict) else {"type": "object", "properties": {}},
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

    async def set_headless(self, headless: bool) -> bool:
        self._headless = headless
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
