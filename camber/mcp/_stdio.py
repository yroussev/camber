"""The stdio transport: newline-delimited JSON-RPC on stdin / stdout, logs on stderr only.

Without a :class:`~camber.mcp.WorkerPool` (library use) messages are answered in order, one at a
time, in this process. With one (``camber mcp``, #292) tool calls run in killable worker processes
and everything else is answered at once by the reading thread; see :func:`serve`.
"""

from __future__ import annotations

import io
import json
import logging
import os
import queue
import re
import select
import sys
import threading
from typing import IO, Any

from ._server import (
    INTERNAL_ERROR,
    INVALID_REQUEST,
    McpServer,
    ToolCall,
    _error,
    _is_lone_surrogate_free,
    _valid_id,
    encode_reply,
)

log = logging.getLogger("camber.mcp")

#: Longest message line accepted, in bytes (newline included). A longer line is answered with an
#: Invalid Request error and skipped without being held in memory.
MAX_MESSAGE_BYTES = 1_048_576


#: one JSON token: a whole string, a structural character, or a run of literal characters
_TOKEN = re.compile(r'\s*(?:("[^"\\]*(?:\\.[^"\\]*)*")|([{}\[\]:,])|([^\s"{}\[\]:,]+))')


def head_id(head: bytes) -> Any:
    """The request id an oversized message line names, read from its first bytes, else ``None``.

    0.111 (#308): a refusal with ``"id": null`` reaches no caller, so the official MCP SDK waited
    for its own timeout. The id is taken only when it is unambiguous: the line starts a JSON
    object, its top level names ``"id"`` exactly once in the bytes read, and the value is a
    complete string or integer (a token cut at the end of ``head`` is not trusted)."""
    text = head.decode("utf-8", errors="replace")
    pos, depth, found, key, expect = 0, 0, [], None, "start"
    while True:
        m = _TOKEN.match(text, pos)
        if m is None or m.end() >= len(text):  # the last token may be cut: never used
            break
        pos = m.end()
        string, struct, literal = m.groups()
        if expect == "start":
            if struct != "{":
                return None
            depth, expect = 1, "key"
            continue
        if struct in ("{", "["):
            if depth == 1 and key == "id":
                return None  # an object or array id is no id
            depth, key = depth + 1, None
            continue
        if struct in ("}", "]"):
            depth -= 1
            if depth == 0:
                break
            continue
        if depth != 1:
            continue
        if expect == "key" and string is not None:
            try:
                key = json.loads(string)
            except ValueError:
                return None
            expect = "colon"
        elif expect == "colon" and struct == ":":
            expect = "value"
        elif expect == "value" and (string is not None or literal is not None):
            if key == "id":
                try:
                    value = json.loads(string if string is not None else literal)
                except ValueError:
                    return None
                found.append(value)
            key, expect = None, "comma"
        elif expect == "comma" and struct == ",":
            expect = "key"
        else:
            return None  # not the JSON-RPC object shape
    if len(found) != 1 or not _valid_id(found[0]):
        return None
    value = found[0]
    if isinstance(value, str) and not _is_lone_surrogate_free(value):
        return None
    return value


#: a run of string characters other than a quote or a backslash
_STR_RUN = re.compile(rb'[^"\\]*')
#: the bytes that change the JSON structure outside a string
_STRUCT = re.compile(rb'["{}\[\],]')


class _TopKeys:
    """Counts the top-level ``"id"`` keys of a JSON object line fed to it in pieces, holding no
    more than a short key at a time (0.111, #318): an oversized line is read and dropped in
    chunks, and its id is answered only when the whole line names it once."""

    def __init__(self):
        self.depth, self.in_str, self.esc, self.expect_key = 0, False, False, False
        self.key: bytearray | None = None
        self.ids = 0

    def _key(self, part: bytes) -> None:
        if self.key is not None:
            self.key = self.key + part if len(self.key) + len(part) <= 64 else None

    def feed(self, data: bytes) -> None:
        i, n = 0, len(data)
        while i < n:
            if self.in_str:
                if self.esc:
                    self.esc = False
                    self._key(data[i : i + 1])
                    i += 1
                    continue
                j = _STR_RUN.match(data, i).end()  # type: ignore[union-attr]  # always matches
                self._key(data[i:j])
                i = j
                if i >= n:
                    break
                if data[i] == 0x5C:  # a backslash: the next byte is escaped
                    self.esc = True
                    self._key(b"\\")
                else:  # the closing quote
                    self.in_str = False
                    if self.key is not None:
                        try:
                            self.ids += json.loads(b'"' + bytes(self.key) + b'"') == "id"
                        except ValueError:
                            pass
                    self.key = None
                i += 1
                continue
            m = _STRUCT.search(data, i)
            if m is None:
                break
            c, i = data[m.start()], m.end()
            if c == 0x22:  # a quote opens a string: a key when the top level expects one
                self.in_str = True
                self.key = bytearray() if self.depth == 1 and self.expect_key else None
                self.expect_key = False
            elif c in b"{[":
                self.depth += 1
                self.expect_key = self.depth == 1 and c == 0x7B
            elif c in b"}]":
                self.depth -= 1
            elif self.depth == 1:  # a comma at the top level: a key follows
                self.expect_key = True


def _oversized(line: bytes, max_message_bytes: int, *, repeated: bool = False) -> dict:
    """The refusal of a message longer than ``max_message_bytes``, answering its id when the line
    names one in its first bytes (:func:`head_id`) and, ``repeated`` false, nowhere else at its
    top level (0.111, #318)."""
    log.warning("refused a message longer than %d bytes", max_message_bytes)
    return _error(
        None if repeated else head_id(line),
        INVALID_REQUEST,
        f"Invalid Request: message longer than {max_message_bytes} bytes",
    )


def _drop_oversized(line: bytes, instream: IO[bytes], max_message_bytes: int) -> tuple:
    """``(refusal, more)`` for an oversized ``line``: the rest of the line is read and dropped
    (``more`` is ``False`` when the stream ended there), its top-level keys counted on the way."""
    keys = _TopKeys()
    keys.feed(line)
    more = line.endswith(b"\n") or _skip_rest_of_line(instream, max_message_bytes, keys)
    return _oversized(line, max_message_bytes, repeated=keys.ids > 1), more


class Terminated(BaseException):
    """Raised in the serving thread by a ``SIGTERM`` / ``SIGHUP`` handler (``camber mcp`` installs
    one, #309): the session stops like a Ctrl-C, and the exit status is ``128 + signum``. Not an
    ``Exception``, as ``KeyboardInterrupt`` is not, so no ``except Exception`` catches it."""

    def __init__(self, signum: int):
        super().__init__(signum)
        self.signum = int(signum)


def _terminated(exc: Terminated) -> int:
    import signal

    try:
        name = signal.Signals(exc.signum).name
    except ValueError:
        name = f"signal {exc.signum}"
    log.info("%s received; exiting", name)
    return 128 + exc.signum


def _skip_rest_of_line(instream: IO[bytes], chunk: int, keys: _TopKeys | None = None) -> bool:
    """Read and drop the remainder of an oversized line, feeding it to ``keys``; ``False`` when
    the stream ended."""
    while True:
        part = instream.readline(chunk)
        if not part:
            return False
        if keys is not None:
            keys.feed(part)
        if part.endswith(b"\n"):
            return True


def _write(outstream: IO[bytes], data: bytes) -> bool:
    """Write and flush one line; ``False`` (logged) when that failed. A closed pipe is the client
    leaving: ``BrokenPipeError`` propagates and ends the loop."""
    try:
        view = memoryview(data)
        while view:  # an unbuffered stream may take part of it (a signal mid-write)
            n = outstream.write(view)
            if n is None:
                break
            view = view[n:]
        outstream.flush()
    except BrokenPipeError:
        raise
    except ValueError:
        if getattr(outstream, "closed", False):  # closed as the session ends: the client is gone
            raise BrokenPipeError("the output stream is closed") from None
        log.exception("could not write a reply")
        return False
    except Exception:
        log.exception("could not write a reply")
        return False
    return True


def _reply(outstream: IO[bytes], response: dict) -> None:
    """Write one reply; when the write fails, try once to answer the same id with an Internal
    error instead, then go on either way: a failed write never ends the session."""
    if _write(outstream, encode_reply(response)):
        return
    id_ = response.get("id")
    if _write(outstream, encode_reply(_error(id_, INTERNAL_ERROR, "Internal error"))):
        return
    log.error("the reply to id %r is lost; continuing", id_)


def serve(
    server: McpServer,
    instream: IO[bytes],
    outstream: IO[bytes],
    *,
    max_message_bytes: int = MAX_MESSAGE_BYTES,
    workers: Any = None,
) -> int:
    """Answer every message line from ``instream`` on ``outstream`` until end of input.

    Without ``workers``, messages are handled in order, one at a time, in this process. With a
    :class:`~camber.mcp.WorkerPool`, each tool call runs in a worker process while the other
    messages (``ping``, ``server/discover``, ``initialize``, the lists, ``prompts/get``) are
    answered at once; replies to concurrent calls may come out of order, each with its own id.
    ``notifications/cancelled`` stops a queued or running call (it gets no reply), a call beyond
    the pool's queue is refused with the retryable tool error ``busy``, and at end of input or
    when the client closes ``outstream`` every call still running is stopped and the pool is
    closed before this returns.

    Each response is one line, flushed at once. Returns 0 at end of input or when the client
    closes ``outstream`` (a broken pipe), 130 on Ctrl-C. Nothing but MCP messages is ever written
    to ``outstream``.
    """
    if workers is not None:
        return _serve_concurrent(server, instream, outstream, max_message_bytes, workers)
    try:
        while True:
            line = instream.readline(max_message_bytes + 1)
            if not line:
                log.info("end of input; exiting")
                return 0
            if len(line) > max_message_bytes:
                refusal, more = _drop_oversized(line, instream, max_message_bytes)
                _reply(outstream, refusal)
                if not more:
                    return 0
                continue
            try:
                response = server.handle_line(line)
            except Exception:  # handle_line answers its own errors; this is a last resort
                log.exception("internal error answering a message")
                response = _error(None, INTERNAL_ERROR, "Internal error")
            if isinstance(response, dict):  # never a ToolCall: nothing is deferred here
                _reply(outstream, response)
    except KeyboardInterrupt:
        log.info("interrupted; exiting")
        return 130
    except Terminated as exc:
        return _terminated(exc)
    except BrokenPipeError:
        log.info("the client closed the output stream; exiting")
        return 0


class _Output:
    """``outstream`` shared by threads: one complete line at a time, and whether the client has
    gone (a broken pipe, or the read end closed)."""

    def __init__(self, outstream: IO[bytes]):
        self.stream = outstream
        self.lock = threading.Lock()
        self.gone = threading.Event()
        self._poll = None
        try:
            fd = outstream.fileno()
            self._poll = select.poll()
            self._poll.register(fd, select.POLLOUT)
        except (AttributeError, OSError, ValueError, io.UnsupportedOperation):
            self._poll = None

    def reply(self, response: dict) -> None:
        if self.gone.is_set():
            return
        with self.lock:
            try:
                _reply(self.stream, response)
            except BrokenPipeError:
                self.gone.set()

    def hung_up(self) -> bool:
        """Whether the client closed its end of ``outstream`` (checked without writing)."""
        if self._poll is None:
            return False
        try:
            events = self._poll.poll(0)
        except OSError:
            return False
        return any(ev & (select.POLLHUP | select.POLLERR) for _, ev in events)


_EOF = object()


def _read_lines(instream: IO[bytes], max_message_bytes: int, lines: queue.Queue) -> None:
    """The reading thread: every line (or the refusal of an oversized one) into ``lines``, then
    ``_EOF``."""
    try:
        while True:
            line = instream.readline(max_message_bytes + 1)
            if not line:
                return
            if len(line) > max_message_bytes:
                refusal, more = _drop_oversized(line, instream, max_message_bytes)
                lines.put(refusal)
                if not more:
                    return
                continue
            lines.put(line)
    except Exception:  # noqa: BLE001 - a read error is the end of the input
        log.exception("could not read the input")
    finally:
        lines.put(_EOF)


def _serve_concurrent(
    server: McpServer, instream: IO[bytes], outstream: IO[bytes], max_bytes: int, workers: Any
) -> int:
    out = _Output(outstream)
    lines: queue.Queue = queue.Queue(maxsize=64)
    inflight: dict = {}  # request id -> [Job]
    inflight_lock = threading.Lock()

    def done(job) -> None:
        with inflight_lock:
            jobs = inflight.get(job.call.id, [])
            if job in jobs:
                jobs.remove(job)
            if not jobs:
                inflight.pop(job.call.id, None)
        out.reply(server.finish(job.call, job.outcome, job.started))

    def cancel(request_id) -> None:
        with inflight_lock:
            jobs = inflight.pop(request_id, [])
        for job in jobs:
            if workers.cancel(job):
                log.info("tool %s (%s): cancelled by the client", job.call.name, job.call.caller)

    previous = server.on_cancel
    server.on_cancel = cancel
    reader = threading.Thread(
        target=_read_lines, args=(instream, max_bytes, lines), name="mcp-stdin", daemon=True
    )
    try:
        workers.start()  # inside: a signal right after it still closes the pool
        reader.start()
        while True:
            if out.gone.is_set() or out.hung_up():
                log.info("the client closed the output stream; exiting")
                return 0
            try:
                item = lines.get(timeout=0.2)
            except queue.Empty:
                continue
            if item is _EOF:
                log.info("end of input; exiting")
                return 0
            if isinstance(item, dict):  # the refusal of an oversized line
                out.reply(item)
                continue
            try:
                response = server.handle_line(item, defer=True)
            except Exception:  # handle_line answers its own errors; this is a last resort
                log.exception("internal error answering a message")
                response = _error(None, INTERNAL_ERROR, "Internal error")
            if isinstance(response, ToolCall):
                with inflight_lock:
                    reused = response.id in inflight
                    job = None if reused else workers.submit(response, done)
                    if job is not None:
                        inflight.setdefault(response.id, []).append(job)
                if reused:  # #309: one reply and one cancel per id
                    log.warning("refused a tools/call reusing the id of a call in progress")
                    out.reply(
                        _error(
                            response.id,
                            INVALID_REQUEST,
                            "Invalid Request: a call with this id is still in progress; "
                            "use a new id for each request",
                        )
                    )
                elif job is None:
                    out.reply(server.finish(response, workers.busy_error()))
            elif response is not None:
                out.reply(response)
    except KeyboardInterrupt:
        log.info("interrupted; exiting")
        return 130
    except Terminated as exc:
        return _terminated(exc)
    finally:
        server.on_cancel = previous
        stopped = workers.close()
        if stopped:
            log.info("stopped %d call(s) still in progress", stopped)


def serve_stdio(
    server: McpServer, *, max_message_bytes: int = MAX_MESSAGE_BYTES, workers: Any = None
) -> int:
    """Serve ``server`` on this process's stdin / stdout (the MCP stdio transport); ``workers``
    as for :func:`serve`.

    The protocol stream keeps a private duplicate of file descriptor 1; for the duration, fd 1 and
    ``sys.stdout`` point at stderr, so a stray ``print`` or a library writing to stdout lands in the
    log instead of corrupting the JSON-RPC stream. Both are restored on return.
    """
    sys.stdout.flush()
    proto_fd = os.dup(1)
    saved_stdout = sys.stdout
    os.dup2(2, 1)
    sys.stdout = sys.stderr
    # unbuffered (#316): closing a buffered stream waits for the lock of a write blocked on a
    # client that stopped reading, so a signal could not end the session; this close does not
    out = os.fdopen(os.dup(proto_fd), "wb", buffering=0)
    # with workers, stdin is read by a thread that may still be blocked in a read when the
    # client closes stdout and this returns: it reads a private duplicate, never sys.stdin, whose
    # lock the interpreter needs at exit (left open: closing it would wait for that read)
    instream = sys.stdin.buffer if workers is None else os.fdopen(os.dup(0), "rb")
    try:
        return serve(server, instream, out, max_message_bytes=max_message_bytes, workers=workers)
    finally:
        try:
            out.close()
        except OSError:  # the client already closed its end
            pass
        sys.stdout = saved_stdout
        os.dup2(proto_fd, 1)
        os.close(proto_fd)
