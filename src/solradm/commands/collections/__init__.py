from async_typer import AsyncTyper

from solradm.commands.callbacks import add_verbosity_option
from solradm.commands.collections import query, reindex
from solradm.commands.collections.data_io import export_documents, import_documents
from solradm.commands.collections.lifecycle import create, delete, depopulate, populate
from solradm.commands.collections.maintenance import reload


app = AsyncTyper()
add_verbosity_option(app)

app.command()(export_documents)
app.command()(import_documents)
app.command()(depopulate)
app.command()(populate)
app.command()(reload)
app.command()(create)
app.command()(delete)
app.command()(query)
app.command()(reindex)