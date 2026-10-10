"""Who may use CAMBER's local HTTP servers: secrets, session cookies, launch files, Host allowlist.

Shared by ``camber lab`` (:mod:`camber.lab._auth`, 0.102, #127) and ``camber serve``
(:mod:`camber.api.server`, 0.103, #128). Both follow one model:

- a random per-run **access token** and **session id** (:func:`new_secret`);
- a launch URL ``...?token=<access token>`` printed to the terminal and saved in a **launch file**
  only its user can read (:func:`write_launch_file`: folder 0700, file 0600, a folder or file
  another user owns or that is group- or world-writable is refused);
- the first ``GET`` with a valid ``?token=`` buys an ``HttpOnly; SameSite=Strict; Path=/`` session
  cookie (:func:`session_cookie`) and a 303 to the URL without the token; a script may send
  ``Authorization: Bearer <access token>`` (:func:`bearer_token`) instead;
- every secret comparison is :func:`hmac.compare_digest` on bytes (:func:`same_secret`);
- a **Host allowlist** against DNS rebinding (:class:`HostAllowlist`): a page on another site whose
  name resolves to this machine still sends its own name in ``Host``, so it is refused.

The launch file lives in ``$XDG_RUNTIME_DIR/camber`` when that is set (Linux), else
``$XDG_CONFIG_HOME/camber`` or ``~/.config/camber``, as ``<prefix>-<port>.url`` (``lab-8765.url``,
``serve-8080.url``).
"""

from __future__ import annotations

import hmac
import ipaddress
import os
import secrets
import stat

#: the query parameter that carries the access token in a launch URL
TOKEN_PARAM = "token"
#: an explicit access token shorter than this is refused (a generated one is 43 characters)
MIN_TOKEN_LEN = 16


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


def session_cookie(name: str, session_id: str) -> str:
    """The ``Set-Cookie`` value for cookie ``name``: a browser-session cookie the page's script
    cannot read (``HttpOnly``), that no other site's request carries (``SameSite=Strict``)."""
    return f"{name}={session_id}; HttpOnly; SameSite=Strict; Path=/"


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


def safe_path(path: str, fallback: str) -> str:
    """``path`` for a same-origin ``Location``: never ``//host`` or a backslash trick."""
    if not path.startswith("/") or path.startswith("//") or any(c in path for c in "\\\r\n"):
        return fallback
    return path


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


def launch_file(port, directory=None, *, prefix: str = "lab") -> str:
    """The ``prefix`` server's launch file on ``port``: ``<launch_dir>/<prefix>-<port>.url``."""
    return os.path.join(launch_dir(directory), f"{prefix}-{int(port)}.url")


def _refuse_unsafe(path: str, st: os.stat_result, kind: str, prefix: str) -> None:
    getuid = getattr(os, "getuid", None)  # POSIX only; Windows has no owner / mode bits here
    if getuid is None:
        return
    if st.st_uid != getuid():
        raise PermissionError(
            f"refusing the {prefix} launch {kind} {path}: it is owned by another user "
            f"(uid {st.st_uid})"
        )
    if st.st_mode & (stat.S_IWGRP | stat.S_IWOTH):
        raise PermissionError(
            f"refusing the {prefix} launch {kind} {path}: it is group- or world-writable "
            f"(mode {stat.S_IMODE(st.st_mode):04o}); make it private (chmod 700 for the folder)"
        )


def _private_dir(directory: str, prefix: str) -> str:
    os.makedirs(directory, mode=0o700, exist_ok=True)
    _refuse_unsafe(directory, os.stat(directory), "folder", prefix)
    if hasattr(os, "getuid"):
        os.chmod(directory, 0o700)
    return directory


def write_launch_file(url: str, port, directory=None, *, prefix: str = "lab") -> str:
    """Save the launch ``url`` to ``<prefix>-<port>.url`` (mode 0600) in a private folder (0700).

    Raises ``PermissionError`` when the folder or an existing launch file is owned by another
    user or is group- or world-writable -- nothing is written then. Returns the file's path.
    """
    path = launch_file(port, directory, prefix=prefix)
    _private_dir(os.path.dirname(path), prefix)
    try:
        _refuse_unsafe(path, os.lstat(path), "file", prefix)
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
    """Remove the launch file at ``path`` if it still holds ``url`` (a newer server's is kept)."""
    if not path:
        return
    try:
        with open(path, encoding="utf-8") as fh:
            if fh.read().strip() != url:
                return
        os.unlink(path)
    except OSError:
        pass


# --------------------------------------------------------------------------- Host allowlist

#: the names a browser uses for this machine's loopback interface
LOOPBACK_NAMES = ("127.0.0.1", "localhost", "[::1]")
#: bind addresses that listen on every interface
WILDCARD_HOSTS = ("0.0.0.0", "::", "")


def _unbracket(host: str) -> str:
    return host[1:-1] if host.startswith("[") and host.endswith("]") else host


def is_wildcard(host: str | None) -> bool:
    """``True`` for a bind address that listens on every interface (``0.0.0.0``, ``::``, ``""``)."""
    return _unbracket(str(host or "").strip()) in WILDCARD_HOSTS


def is_loopback(host: str | None) -> bool:
    """``True`` for ``localhost`` or a loopback IP literal (``127.0.0.0/8``, ``::1``)."""
    h = _unbracket(str(host or "").strip().lower())
    if h == "localhost":
        return True
    try:
        return ipaddress.ip_address(h).is_loopback
    except ValueError:
        return False


def host_name(host: str) -> str:
    """``host`` as it appears in a ``Host`` header: lower-case, an IPv6 literal in brackets."""
    h = _unbracket(str(host).strip().lower())
    return f"[{h}]" if ":" in h else h


def split_host(value: str) -> tuple[str, str | None]:
    """``"name:port"`` -> ``("name", "port")``; ``"[::1]:80"`` -> ``("[::1]", "80")``;
    a bare name or IPv6 literal -> ``(name, None)``. The name is lower-cased."""
    v = str(value).strip().lower()
    if v.startswith("["):
        end = v.find("]")
        if end < 0:
            return v, None
        rest = v[end + 1 :]
        return v[: end + 1], (rest[1:] if rest.startswith(":") else None)
    if v.count(":") > 1:  # a bare IPv6 literal (no brackets, no port)
        return f"[{v}]", None
    name, sep, port = v.partition(":")
    return name, (port if sep else None)


class HostAllowlist:
    """The ``Host`` values a server answers (DNS rebinding defence).

    - **Derived** entries match ``name:<bound port>`` exactly: the loopback names
      (``127.0.0.1``, ``localhost``, ``[::1]``) when bound to loopback or to every interface, and
      the bound host itself.
    - **Explicit** entries (``--allow-host``, ``CAMBER_API_ALLOWED_HOSTS``) are ``name`` or
      ``name:port``. A bare name matches that name on any port or none (a proxy, a remapped
      container port); ``name:port`` matches exactly. ``*`` allows any ``Host`` (unsafe: it turns
      the rebinding defence off).
    """

    def __init__(self, bind_host: str, port: int, extra=()):
        self.port = int(port)
        self.any = False
        self.exact: set = set()
        self.names: set = set()
        bind = str(bind_host or "").strip()
        if is_loopback(bind) or is_wildcard(bind):
            self.exact.update(f"{n}:{self.port}" for n in LOOPBACK_NAMES)
        if not is_wildcard(bind):
            self.exact.add(f"{host_name(bind)}:{self.port}")
        for raw in extra or ():
            item = str(raw).strip()
            if not item:
                continue
            if item == "*":
                self.any = True
                continue
            name, p = split_host(item)
            if p is None:
                self.names.add(name)
            else:
                self.exact.add(f"{name}:{p}")

    def allows(self, header: str | None) -> bool:
        """``True`` when a request with ``Host: header`` may be answered."""
        if self.any:
            return True
        if not header:
            return False
        value = str(header).strip().lower()
        if value in self.exact:
            return True
        name, _ = split_host(value)
        return name in self.names

    def describe(self) -> str:
        """A one-line summary for the startup banner."""
        if self.any:
            return "any Host (--allow-host '*': the DNS-rebinding check is off)"
        return ", ".join(sorted(self.exact) + sorted(f"{n} (any port)" for n in self.names))


def parse_host_list(value: str | None) -> list:
    """``"a, b:8080,,c"`` -> ``["a", "b:8080", "c"]`` (the ``CAMBER_API_ALLOWED_HOSTS`` form)."""
    return [p.strip() for p in str(value or "").split(",") if p.strip()]
