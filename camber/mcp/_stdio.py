"""The stdio transport: newline-delimited JSON-RPC on stdin / stdout, logs on stderr only."""

from __future__ import annotations

import logging
import os
import sys
from typing import IO

from ._server import INTERNAL_ERROR, INVALID_REQUEST, McpServer, _error, encode_reply

log = logging.getLogger("camber.mcp")

#: Longest message line accepted, in bytes (newline included). A longer line is answered with an
#: Invalid Request error and skipped without being held in memory.
MAX_MESSAGE_BYTES = 1_048_576


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
) -> int:
    """Answer every message line from ``instream`` on ``outstream`` until end of input.

    Messages are handled in order, one at a time; each response is one line, flushed at once.
    Returns 0 at end of input or when the client closes ``outstream`` (a broken pipe), 130 on
    Ctrl-C. Nothing but MCP messages is ever written to ``outstream``.
    """
    try:
        while True:
            line = instream.readline(max_message_bytes + 1)
            if not line:
                log.info("end of input; exiting")
                return 0
            if len(line) > max_message_bytes:
                more = line.endswith(b"\n") or _skip_rest_of_line(instream, max_message_bytes)
                refusal = _error(
                    None,
                    INVALID_REQUEST,
                    f"Invalid Request: message longer than {max_message_bytes} bytes",
                )
                log.warning("refused a message longer than %d bytes", max_message_bytes)
                _reply(outstream, refusal)
                if not more:
                    return 0
                continue
            try:
                response = server.handle_line(line)
            except Exception:  # handle_line answers its own errors; this is a last resort
                log.exception("internal error answering a message")
                response = _error(None, INTERNAL_ERROR, "Internal error")
            if response is not None:
                _reply(outstream, response)
    except KeyboardInterrupt:
        log.info("interrupted; exiting")
        return 130
    except BrokenPipeError:
        log.info("the client closed the output stream; exiting")
        return 0


def serve_stdio(server: McpServer, *, max_message_bytes: int = MAX_MESSAGE_BYTES) -> int:
    """Serve ``server`` on this process's stdin / stdout (the MCP stdio transport).

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
    try:
        return serve(server, sys.stdin.buffer, out, max_message_bytes=max_message_bytes)
    finally:
        try:
            out.close()
        except OSError:  # the client already closed its end
            pass
        sys.stdout = saved_stdout
        os.dup2(proto_fd, 1)
        os.close(proto_fd)
