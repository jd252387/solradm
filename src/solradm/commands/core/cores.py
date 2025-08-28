from typing import Annotated

import typer
from async_typer import AsyncTyper

from solradm.api.core import reload_core
from solradm.renderers.task_table import MultiTaskTable
from solradm.tasks.metatask import MetaTask
from solradm.tasks.multimetatask import MultiMetaTask
from solradm.utils.cores import get_cores

app = AsyncTyper()

@app.async_command()
async def full_reload(
    collection: Annotated[str, typer.Option()]
):
    import asyncio
    pending = await get_cores(collection)
    tasks = [
        MetaTask(
            [descriptor.base_url, descriptor.core_name],
            asyncio.create_task(reload_core(descriptor)),
        )
        for descriptor in pending
    ]
    table = MultiTaskTable(MultiMetaTask(["host", "core"], tasks), refresh_every=0.25)
    await asyncio.gather(*[task.task for task in tasks])
    table.stop()