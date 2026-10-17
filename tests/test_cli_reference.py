"""docs/CLI.md names every command and option the CLI parser accepts (#224)."""

import argparse
import re
from pathlib import Path

from camber.cli import _build_parser

CLI_MD = (Path(__file__).resolve().parents[1] / "docs" / "CLI.md").read_text(encoding="utf-8")

#: Options deliberately left out of docs/CLI.md, as {(command, option): reason}. Empty today:
#: every option is documented, deprecated aliases included (retention apply's --wait).
HIDDEN: dict = {}


def _walk(parser, path=()):
    """Yield (command path, option string or None for the command itself)."""
    for action in parser._actions:
        if isinstance(action, argparse._SubParsersAction):
            for name, sub in action.choices.items():
                yield path + (name,), None
                yield from _walk(sub, path + (name,))
        else:
            for opt in action.option_strings:
                if opt not in ("-h", "--help"):
                    yield path, opt


def _named(token: str) -> bool:
    return re.search(r"(?<![\w-])" + re.escape(token) + r"(?![\w-])", CLI_MD) is not None


ENTRIES = list(_walk(_build_parser()))


def test_the_walk_sees_the_parser():
    commands = {" ".join(p) for p, opt in ENTRIES if opt is None}
    assert {"run", "mv freeze", "datasets ingest", "bacnet-discover"} <= commands
    assert (("mv", "freeze"), "--allow-short") in ENTRIES


def test_every_command_is_named():
    missing = sorted(" ".join(p) for p, opt in ENTRIES if opt is None and not _named(p[-1]))
    assert not missing, f"commands docs/CLI.md never names: {missing}"


def test_every_option_is_named():
    missing = sorted(
        {
            (" ".join(p), opt)
            for p, opt in ENTRIES
            if opt is not None and (" ".join(p), opt) not in HIDDEN and not _named(opt)
        }
    )
    assert not missing, (
        f"options docs/CLI.md never names (document them, or list in HIDDEN): {missing}"
    )


def test_hidden_options_still_exist():
    present = {(" ".join(p), opt) for p, opt in ENTRIES if opt is not None}
    assert set(HIDDEN) <= present, sorted(set(HIDDEN) - present)
