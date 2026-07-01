from typer import Typer

from solradm.commands.callbacks import add_verbosity_option

app = Typer()
add_verbosity_option(app)
