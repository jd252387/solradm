import rich

from solradm import __version__


def version() -> None:
    """Display the current solradm version."""
    rich.print(__version__)
