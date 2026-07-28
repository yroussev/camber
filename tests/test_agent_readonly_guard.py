"""Structural guards for the agent + mapping-assist layer.

Two AST checks, enforced on real code rather than docstring prose:

1. **Read-only:** these modules must never *reference* a write/command/actuation service — the agent
   explains and the mapping-assist advises; neither ever writes back to a BAS.
2. **No vendor / no network:** no LLM provider SDK and no network client may be imported anywhere in
   the pure layer. All model I/O is owned by the caller's injected ``complete`` callable, so a
   vendor import here would break the provider-agnostic guarantee.
"""

import ast
import glob
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def _guarded_files():
    files = sorted(glob.glob(os.path.join(_ROOT, "camber", "agent", "*.py")))
    files.append(os.path.join(_ROOT, "camber", "mapping_assist.py"))
    return files


# write/command/actuation vocabulary — the read-only contract
_FORBIDDEN_SYMBOLS = {
    "write_back",
    "writeback",
    "write_property",
    "writeproperty",
    "WriteProperty",
    "write_register",
    "write_registers",
    "write_coil",
    "write_coils",
    "write_value",
    "write_values",
    "set_value",
    "write_attribute",
    "command",
    "actuate",
    "override",
    "publish",
    "send_command",
    "set_point",
    "setpoint_write",
}

# LLM providers + network clients — the no-vendor / no-network contract
_FORBIDDEN_IMPORT_ROOTS = {
    "anthropic",
    "openai",
    "cohere",
    "google",
    "mistralai",
    "ollama",
    "langchain",
    "llama_cpp",
    "transformers",
    "httpx",
    "requests",
    "urllib",
    "socket",
    "aiohttp",
    "http",
    "websocket",
    "websockets",
}


def test_agent_layer_references_no_write_services():
    for path in _guarded_files():
        with open(path, encoding="utf-8") as fh:
            tree = ast.parse(fh.read())
        names = set()
        for node in ast.walk(tree):
            if isinstance(node, ast.Attribute):
                names.add(node.attr)
            elif isinstance(node, ast.Name):
                names.add(node.id)
        bad = names & _FORBIDDEN_SYMBOLS
        assert not bad, f"{path} references write/command service(s): {bad}"


def test_agent_layer_imports_no_vendor_or_network():
    for path in _guarded_files():
        with open(path, encoding="utf-8") as fh:
            tree = ast.parse(fh.read())
        for node in ast.walk(tree):
            roots = []
            if isinstance(node, ast.Import):
                roots = [alias.name.split(".")[0] for alias in node.names]
            elif isinstance(node, ast.ImportFrom) and node.module and node.level == 0:
                roots = [node.module.split(".")[0]]
            bad = set(roots) & _FORBIDDEN_IMPORT_ROOTS
            assert not bad, f"{path} imports a vendor/network module: {bad}"


def test_guard_covers_expected_modules():
    # the guard must actually be looking at the agent package + mapping_assist (not silently empty)
    basenames = {os.path.basename(p) for p in _guarded_files()}
    assert {
        "context.py",
        "verify.py",
        "templates.py",
        "client.py",
        "explain.py",
        "ask.py",
        "mapping_assist.py",
    } <= basenames
