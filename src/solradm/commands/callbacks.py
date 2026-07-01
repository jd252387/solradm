from typing import Union

import typer
from solradm.async_typer import AsyncTyper

import solradm.api.utils as api_utils

AppType = Union[typer.Typer, AsyncTyper]


def add_verbosity_option(app: AppType) -> None:
    @app.callback()
    def _verbosity(verbose: bool = typer.Option(False, "--verbose", "-v", help="Log HTTP requests")):
        api_utils.log_requests = verbose
