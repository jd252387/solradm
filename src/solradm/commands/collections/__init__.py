from async_typer import AsyncTyper

from solradm.commands.callbacks import add_verbosity_option

app = AsyncTyper()
add_verbosity_option(app)