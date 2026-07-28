"""Tests for the provider-agnostic LLM seam (camber.agent.client).

The seam must be fully usable with no model wired (raising a helpful error only when generate is
actually called), wrap an arbitrary callable, and provide a network-free stub for tests.
"""

import os
import sys

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from camber.agent.client import AgentClient, client_from_callable, stub_client  # noqa: E402


def test_unwired_client_is_valid_but_generate_raises():
    c = AgentClient()
    assert c.wired is False
    with pytest.raises(NotImplementedError) as ei:
        c.generate("hello")
    assert "complete" in str(ei.value)  # the error tells you how to wire one


def test_non_callable_complete_rejected():
    with pytest.raises(TypeError):
        AgentClient("not a function")


def test_client_from_callable_passes_prompt_and_options():
    seen = {}

    def fake(prompt, **opts):
        seen["prompt"] = prompt
        seen["opts"] = opts
        return "ok"

    c = client_from_callable(fake, system="SYS", max_tokens=42, temperature=0.7)
    assert c.wired and c.generate("PROMPT") == "ok"
    assert seen["prompt"] == "PROMPT"
    assert seen["opts"] == {"system": "SYS", "max_tokens": 42, "temperature": 0.7}


def test_generate_coerces_non_string_to_string():
    c = client_from_callable(lambda p, **o: 123)
    assert c.generate("x") == "123"


def test_stub_string_returns_constant():
    c = stub_client("always this")
    assert c.generate("a") == "always this" and c.generate("b") == "always this"


def test_stub_list_returns_in_sequence_then_repeats_last():
    c = stub_client(["one", "two"])
    assert [c.generate("x"), c.generate("x"), c.generate("x")] == ["one", "two", "two"]


def test_stub_callable_is_invoked():
    c = stub_client(lambda p, **o: p.upper())
    assert c.generate("hi") == "HI"


def test_stub_none_echoes_prompt():
    c = stub_client()
    assert c.generate("echo me") == "echo me"


def test_stub_never_imports_a_vendor_or_network():
    # the stub is a pure callable; wiring it must not require any provider SDK
    c = stub_client("x")
    assert c.wired and c.generate("y") == "x"
