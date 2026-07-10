"""The provider-agnostic LLM seam — an injected ``complete`` callable, no vendor, no SDK, no network.

CAMBER never names, imports, or bundles an LLM provider. To use a model you wrap *your* vendor SDK in a
callable and hand it in — exactly as ``ingest.haystack`` takes an injected ``his_read`` transport
instead of importing a BAS client. The contract is one function::

    complete(prompt: str, **opts) -> str

where ``opts`` carries ``system`` / ``max_tokens`` / ``temperature`` (the caller's wrapper uses what
its SDK understands and ignores the rest). Until a callable is wired, :meth:`AgentClient.generate`
raises a helpful :class:`NotImplementedError` — the layer still works via the deterministic templates.

Pure: no imports beyond stdlib typing; the injected callable owns all I/O.
"""

from __future__ import annotations


class AgentClient:
    """Wraps an injected ``complete(prompt, **opts) -> str`` callable with generation options.

    ``complete=None`` is a valid, fully-usable state: :attr:`wired` is False and callers fall back to
    the deterministic template layer. The options are passed through to the callable as keywords; a
    caller's wrapper is free to use or ignore each.
    """

    def __init__(self, complete=None, *, system: str = "", max_tokens: int = 1024,
                 temperature: float = 0.0):
        if complete is not None and not callable(complete):
            raise TypeError("complete must be a callable (prompt, **opts) -> str, or None")
        self._complete = complete
        self.system = system
        self.max_tokens = max_tokens
        self.temperature = temperature

    @property
    def wired(self) -> bool:
        """True when a ``complete`` callable is present (an LLM can actually be called)."""
        return self._complete is not None

    def generate(self, prompt: str) -> str:
        """Call the injected model with the configured options; raise if none is wired."""
        if self._complete is None:
            raise NotImplementedError(
                "No LLM is wired into this AgentClient. Pass a callable "
                "`complete(prompt, **opts) -> str` that wraps your provider's SDK — e.g. "
                "`client_from_callable(lambda p, **o: my_sdk.complete(p))`. CAMBER intentionally "
                "ships no provider; the deterministic template layer works without one.")
        out = self._complete(prompt, system=self.system, max_tokens=self.max_tokens,
                             temperature=self.temperature)
        return out if isinstance(out, str) else str(out)


def client_from_callable(fn, *, system: str = "", max_tokens: int = 1024,
                         temperature: float = 0.0) -> AgentClient:
    """Build an :class:`AgentClient` from a ``complete(prompt, **opts) -> str`` callable."""
    return AgentClient(fn, system=system, max_tokens=max_tokens, temperature=temperature)


def stub_client(scripted=None, **kw) -> AgentClient:
    """A network-free client for tests/demos.

    ``scripted`` may be: a **str** (returned for every call); a **list** (returned in sequence, then
    the last repeats); a **callable** ``(prompt, **opts) -> str``; or **None** (echoes the prompt
    back). Never touches a network — the whole point of the injected seam.
    """
    if callable(scripted):
        fn = scripted
    elif isinstance(scripted, str):
        def fn(prompt, **opts):
            return scripted
    elif isinstance(scripted, (list, tuple)):
        seq = list(scripted)
        state = {"i": 0}

        def fn(prompt, **opts):
            i = min(state["i"], len(seq) - 1) if seq else 0
            state["i"] += 1
            return seq[i] if seq else ""
    else:
        def fn(prompt, **opts):
            return prompt                    # echo
    return AgentClient(fn, **kw)
