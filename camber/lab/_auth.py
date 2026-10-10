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

0.103 (#128): the implementation moved to :mod:`camber._access`, shared with ``camber serve
--auth token``; this module keeps the lab's names, defaults and behaviour unchanged.
"""

from __future__ import annotations

from .. import _access
from .._access import (
    MIN_TOKEN_LEN,
    TOKEN_PARAM,
    bearer_token,
    cookie_value,
    launch_dir,
    new_secret,
    remove_launch_file,
    same_secret,
)

#: the session cookie is ``camber-lab-<port>``: browsers share cookies across the ports of one
#: host, so two labs on two ports keep separate sessions
COOKIE_PREFIX = "camber-lab-"
_LAUNCH_PREFIX = "lab"

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


def cookie_name(port) -> str:
    """The session cookie's name for the lab on ``port``."""
    return f"{COOKIE_PREFIX}{port}"


def session_cookie(port, session_id: str) -> str:
    """The ``Set-Cookie`` value: a browser-session cookie the page's script cannot read
    (``HttpOnly``), that no other site's request carries (``SameSite=Strict``)."""
    return _access.session_cookie(cookie_name(port), session_id)


def launch_file(port, directory=None) -> str:
    """The launch file of the lab on ``port``: ``<launch_dir>/lab-<port>.url``."""
    return _access.launch_file(port, directory, prefix=_LAUNCH_PREFIX)


def write_launch_file(url: str, port, directory=None) -> str:
    """Save the launch ``url`` to ``lab-<port>.url`` (mode 0600) in a private folder (0700).

    Raises ``PermissionError`` when the folder or an existing launch file is owned by another
    user or is group- or world-writable -- nothing is written then. Returns the file's path.
    """
    return _access.write_launch_file(url, port, directory, prefix=_LAUNCH_PREFIX)
