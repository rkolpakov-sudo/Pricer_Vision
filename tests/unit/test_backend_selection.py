"""Unit tests for browser backend selection in MCPBridge."""

from src.mcp_bridge import resolve_backends


def test_defaults_when_empty():
    assert resolve_backends({}) == ["camoufox", "playwright", "nodriver"]


def test_primary_always_first():
    cfg = {"browser": {"backend": "nodriver", "backends": ["camoufox", "nodriver", "playwright"]}}
    assert resolve_backends(cfg) == ["nodriver", "camoufox", "playwright"]


def test_primary_not_in_chain_is_prepended():
    cfg = {"browser": {"backend": "nodriver", "backends": ["camoufox", "playwright"]}}
    assert resolve_backends(cfg) == ["nodriver", "camoufox", "playwright"]


def test_unknown_names_dropped_and_defaults_completed():
    cfg = {"browser": {"backend": "bogus", "backends": ["camoufox", "unknown", "nodriver"]}}
    assert resolve_backends(cfg) == ["camoufox", "nodriver", "playwright"]


def test_backend_only_uses_default_chain_with_primary_first():
    cfg = {"browser": {"backend": "nodriver"}}
    assert resolve_backends(cfg) == ["nodriver", "camoufox", "playwright"]


def test_duplicates_removed():
    cfg = {"browser": {"backend": "camoufox", "backends": ["camoufox", "camoufox", "nodriver", "nodriver"]}}
    assert resolve_backends(cfg) == ["camoufox", "nodriver", "playwright"]


def test_result_never_empty():
    cfg = {"browser": {"backend": "bogus", "backends": ["nope", "invalid"]}}
    assert resolve_backends(cfg) == ["camoufox", "playwright", "nodriver"]
