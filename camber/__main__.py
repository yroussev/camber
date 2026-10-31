"""The ``camber`` console script (and ``python -m camber``).

Kept this small, importing nothing, so a Ctrl-C while :mod:`camber.cli` loads is caught here and
ends the command quietly with exit status 130, as one while it runs does (#322).
"""


def main(argv=None):
    """Load :mod:`camber.cli` and run its ``main``; Ctrl-C at any point gives exit status 130."""
    try:
        from .cli import main as run

        return run(argv)
    except KeyboardInterrupt:  # while the CLI loaded, or before its own handling began
        return 130


if __name__ == "__main__":
    raise SystemExit(main())
