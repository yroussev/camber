"""The stdio transport: newline-delimited JSON-RPC on stdin / stdout, logs on stderr only."""

from __future__ import annotations

import logging
import os
import sys
from typing import IO

from ._server import INVALID_REQUEST, McpServer, _error, encode_message

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
                outstream.write(encode_message(refusal))
                outstream.flush()
                if not more:
                    return 0
                continue
            response = server.handle_line(line)
            if response is not None:
                outstream.write(encode_message(response))
                outstream.flush()
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
