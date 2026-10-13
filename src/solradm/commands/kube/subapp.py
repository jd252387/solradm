import urllib3

from solradm.async_typer import AsyncTyper
from solradm.commands.callbacks import add_verbosity_option

urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)

app = AsyncTyper()
add_verbosity_option(app)
