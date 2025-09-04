import rich
from rich.pretty import pprint
from typer import Typer

from solradm.commands.callbacks import add_verbosity_option
from solradm.config import settings, persist
from solradm.config.interactive import setup_solrauth

app = Typer()
add_verbosity_option(app)


@app.command()
def edit():
    """Edit stored Solr credentials"""
    auth = setup_solrauth.setup()
    settings.set("auth", {"user": auth.login, "password": auth.password})
    persist()
    rich.print("[success]✅  Updated Solr credentials!")


@app.command()
def view():
    """View stored Solr credentials"""
    pprint({"user": settings.auth.user, "password": settings.auth.password})
