import asyncio
from unittest.mock import patch, AsyncMock
import pytest
from src.mcp_bridge import MCPBridge


class TestMCPBridge:
    def test_initial_state(self):
        bridge = MCPBridge()
        assert bridge._servers == []
        assert bridge._stopped is False
        assert bridge._lock is not None

    @pytest.mark.asyncio
    async def test_call_tool_before_start(self):
        bridge = MCPBridge()
        result = await bridge.call_tool("navigate", {"url": "http://example.com"})
        assert "error" in result

    @pytest.mark.asyncio
    async def test_health_check_before_start(self):
        bridge = MCPBridge()
        assert await bridge.health_check() is False

    @pytest.mark.asyncio
    async def test_restart_stops_and_returns_bool(self):
        bridge = MCPBridge()
        ok = await bridge.restart()
        assert isinstance(ok, bool)

    @pytest.mark.asyncio
    async def test_stop_idempotent(self):
        bridge = MCPBridge()
        await bridge.stop()
        assert bridge._stopped is True
        await bridge.stop()
        assert bridge._stopped is True

    def test_lock_exists(self):
        bridge = MCPBridge()
        assert hasattr(bridge, "_lock")

    @pytest.mark.asyncio
    async def test_start_fails_gracefully_when_no_server(self):
        bridge = MCPBridge()
        ok = await bridge.start()
        assert isinstance(ok, bool)


@pytest.mark.asyncio
async def test_concurrent_call_tool_returns_error():
    b1 = MCPBridge()
    b2 = MCPBridge()
    r1, r2 = await asyncio.gather(
        b1.call_tool("snapshot", {}),
        b2.call_tool("snapshot", {}),
    )
    assert "error" in r1
    assert "error" in r2
