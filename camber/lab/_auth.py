"""Who may use a ``camber lab``: the per-run access token, the session cookie, the launch file.

The lab binds ``127.0.0.1``, but on a computer shared by several accounts every local user can
reach a loopback port (#127). So every request -- every GET as well as every POST, the delegated
read routes included -- must prove it comes from the person who started the lab:

- At startup :class:`~camber.lab.LabApp` makes a random **access token** (separate from the CSRF
  token the page carries) and a random **session id**. ``camber lab`` prints the launch URL,
  ``http://127.0.0.1:<port>/lab?token=<access token>``, to its terminal and saves it in a
  **launch file** only its user can read (:func:`write_launch_file`).
- The first ``GET`` carrying a valid ``?token=`` gets a session cookie (:func:`session_cookie`:
  ``HttpOnly; SameSite=Strict; Path=/``) and a 303 to the same URL without the token, so the
  secret does not stay in the address bar or the history.
- After that the browser sends the cookie. A script may send ``Authorization: Bearer <access
  token>`` instead. A request with neither is 401; a wrong ``?token=`` or bearer token is 403.

Every secret comparison is :func:`hmac.compare_digest` on bytes (:func:`same_secret`).

The launch file lives in ``$XDG_RUNTIME_DIR/camber`` when that is set (Linux), else
``$XDG_CONFIG_HOME/camber`` or ``~/.config/camber``, as ``lab-<port>.url``. The directory is made
0700 and the file 0600 (written to a fresh temporary file, then renamed into place); a directory
or an existing file that another user owns, or that is group- or world-writable, is refused.
"""

from __future__ import annotations

import hmac
import os
import secrets
import stat

#: the session cookie is ``camber-lab-<port>``: browsers share cookies across the ports of one
#: host, so two labs on two ports keep separate sessions
COOKIE_PREFIX = "camber-lab-"
#: the query parameter that carries the access token in the launch URL
TOKEN_PARAM = "token"
#: an explicit access token shorter than this is refused (the generated one is 43 characters)
MIN_TOKEN_LEN = 16

__all__ = [
    "COOKIE_PREFIX",
    "TOKEN_PARAM",
    "MIN_TOKEN_LEN",
    "new_secret",
    "same_secret",
    "cookie_name",
    "session_cookie",
    "cookie_value",
    "bearer_token",
    "launch_dir",
    "launch_file",
    "write_launch_file",
    "remove_launch_file",
]


def new_secret() -> str:
    """A fresh 256-bit URL-safe secret."""
    return secrets.token_urlsafe(32)


def same_secret(given, expected: str) -> bool:
    """``given == expected`` in constant time; ``False`` for ``None`` or an empty ``given``."""
    if not given or not expected:
        return False
    return hmac.compare_digest(
        str(given).encode("utf-8", "replace"), str(expected).encode("utf-8", "replace")
    )


def cookie_name(port) -> str:
    """The session cookie's name for the lab on ``port``."""
    return f"{COOKIE_PREFIX}{port}"


def session_cookie(port, session_id: str) -> str:
    """The ``Set-Cookie`` value: a browser-session cookie the page's script cannot read
    (``HttpOnly``), that no other site's request carries (``SameSite=Strict``)."""
    return f"{cookie_name(port)}={session_id}; HttpOnly; SameSite=Strict; Path=/"


def cookie_value(header: str | None, name: str) -> list:
    """Every value of cookie ``name`` in a ``Cookie`` request header (tolerant parse)."""
    out = []
    for part in (header or "").split(";"):
        key, sep, val = part.strip().partition("=")
        if sep and key.strip() == name:
            out.append(val.strip().strip('"'))
    return out


def bearer_token(header: str | None) -> str | None:
    """The token of an ``Authorization: Bearer <token>`` header, else ``None``."""
    scheme, _, value = (header or "").strip().partition(" ")
    if scheme.lower() != "bearer" or not value.strip():
        return None
    return value.strip()


# --------------------------------------------------------------------------- the launch file


def launch_dir(override=None) -> str:
    """Where launch files go: ``override``, else ``$XDG_RUNTIME_DIR/camber``, else
    ``$XDG_CONFIG_HOME/camber``, else ``~/.config/camber``."""
    if override:
        return os.path.abspath(os.fspath(override))
    runtime = os.environ.get("XDG_RUNTIME_DIR")
    if runtime:
        return os.path.join(runtime, "camber")
    cfg = os.environ.get("XDG_CONFIG_HOME") or os.path.join(os.path.expanduser("~"), ".config")
    return os.path.join(cfg, "camber")


def launch_file(port, directory=None) -> str:
    """The launch file of the lab on ``port``: ``<launch_dir>/lab-<port>.url``."""
    return os.path.join(launch_dir(directory), f"lab-{int(port)}.url")


def _refuse_unsafe(path: str, st: os.stat_result, kind: str) -> None:
    getuid = getattr(os, "getuid", None)  # POSIX only; Windows has no owner / mode bits here
    if getuid is None:
        return
    if st.st_uid != getuid():
        raise PermissionError(
            f"refusing the lab launch {kind} {path}: it is owned by another user (uid {st.st_uid})"
        )
    if st.st_mode & (stat.S_IWGRP | stat.S_IWOTH):
        raise PermissionError(
            f"refusing the lab launch {kind} {path}: it is group- or world-writable "
            f"(mode {stat.S_IMODE(st.st_mode):04o}); make it private (chmod 700 for the folder)"
        )


def _private_dir(directory: str) -> str:
    os.makedirs(directory, mode=0o700, exist_ok=True)
    _refuse_unsafe(directory, os.stat(directory), "folder")
    if hasattr(os, "getuid"):
        os.chmod(directory, 0o700)
    return directory


def write_launch_file(url: str, port, directory=None) -> str:
    """Save the launch ``url`` to ``lab-<port>.url`` (mode 0600) in a private folder (0700).

    Raises ``PermissionError`` when the folder or an existing launch file is owned by another
    user or is group- or world-writable -- nothing is written then. Returns the file's path.
    """
    path = launch_file(port, directory)
    _private_dir(os.path.dirname(path))
    try:
        _refuse_unsafe(path, os.lstat(path), "file")
    except FileNotFoundError:
        pass
    tmp = f"{path}.{os.getpid()}.{secrets.token_hex(4)}.tmp"
    fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    try:
        if hasattr(os, "fchmod"):
            os.fchmod(fd, 0o600)
        os.write(fd, (url + "\n").encode("utf-8"))
        os.fsync(fd)
    finally:
        os.close(fd)
    try:
        os.replace(tmp, path)
    except OSError:
        os.unlink(tmp)
        raise
    return path


def remove_launch_file(path: str | None, url: str) -> None:
    """Remove the launch file at ``path`` if it still holds ``url`` (a newer lab's is kept)."""
    if not path:
        return
    try:
        with open(path, encoding="utf-8") as fh:
            if fh.read().strip() != url:
                return
        os.unlink(path)
    except OSError:
        pass
