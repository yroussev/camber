"""Read API for the time-series store (capability-map §8)."""

from .read import BadQuery, ReadAPI
from .server import dispatch, make_server, serve

__all__ = ["BadQuery", "ReadAPI", "dispatch", "make_server", "serve"]
