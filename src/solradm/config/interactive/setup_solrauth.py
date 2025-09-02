"""Interactive helper to configure Solr authentication."""

from typing import TYPE_CHECKING

if TYPE_CHECKING:  # pragma: no cover - for type checking only
    from aiohttp import BasicAuth


def setup() -> "BasicAuth":
    """Prompt the user for credentials and return an aiohttp auth object."""
    from aiohttp import BasicAuth
    from rich.prompt import Prompt

    username = ""
    while username == "":
        username = Prompt.ask("[question]Enter your Solr username -> ")
    password = ""
    while password == "":
        password = Prompt.ask("[question]Enter your Solr password -> ")

    return BasicAuth(username, password)

