"""Manage stored Solr authentication credentials."""

from typer import Typer

from solradm.config import persist, settings
from solradm.lazy import lazy_module

setup_solrauth = lazy_module("solradm.config.interactive.setup_solrauth")

app = Typer()


@app.command()
def edit():
    """Edit stored Solr credentials."""
    auth = setup_solrauth.setup()
    settings.set("auth", {"user": auth.login, "password": auth.password})
    persist()
    from rich import print as rprint

    rprint("[success]✅  Updated Solr credentials!")

@app.command()
def view():
    """View stored Solr credentials."""
    from rich.pretty import pprint

    pprint({"user": settings.auth.user, "password": settings.auth.password})
