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


def _oversized(line: bytes, max_message_bytes: int) -> dict:
    """The refusal of a message longer than ``max_message_bytes``, answering its id when the line
    names one in its first bytes (:func:`head_id`)."""
    log.warning("refused a message longer than %d bytes", max_message_bytes)
    return _error(
        head_id(line),
        INVALID_REQUEST,
        f"Invalid Request: message longer than {max_message_bytes} bytes",
    )


def _skip_rest_of_line(instream: IO[bytes], chunk: int) -> bool:
    """Read and drop the remainder of an oversized line; ``False`` when the stream ended."""
    while True:
        part = instream.readline(chunk)
        if not part:
            return False
        if part.endswith(b"\n"):
            return True


def _write(outstream: IO[bytes], data: bytes) -> bool:
    """Write and flush one line; ``False`` (logged) when that failed. A closed pipe is the client
    leaving: ``BrokenPipeError`` propagates and ends the loop."""
    try:
        outstream.write(data)
        outstream.flush()
    except BrokenPipeError:
        raise
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
                more = line.endswith(b"\n") or _skip_rest_of_line(instream, max_message_bytes)
                _reply(outstream, _oversized(line, max_message_bytes))
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
                more = line.endswith(b"\n") or _skip_rest_of_line(instream, max_message_bytes)
                lines.put(_oversized(line, max_message_bytes))
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
    workers.start()
    reader.start()
    try:
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
                    job = workers.submit(response, done)
                    if job is not None:
                        inflight.setdefault(response.id, []).append(job)
                if job is None:
                    out.reply(server.finish(response, workers.busy_error()))
            elif response is not None:
                out.reply(response)
    except KeyboardInterrupt:
        log.info("interrupted; exiting")
        return 130
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
    out = os.fdopen(os.dup(proto_fd), "wb")
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
