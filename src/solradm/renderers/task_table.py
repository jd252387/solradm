import asyncio
from asyncio import Task
from typing import List

from rich.console import Group
from rich.live import Live
from rich.panel import Panel
from rich.table import Table
from enum import Enum

from solradm.tasks.multimetatask import MultiMetaTask
from solradm.renderers.renderer import Renderer


class TaskResult(Enum):
    PENDING = "⌛ Pending"
    DONE = "✅ Done"
    FAILED = "❌ Failed"

class MultiTaskTable(Renderer):
    def __exit__(self, exc_type, exc_value, traceback, /):
        pass

    _task: Task
    _metatasks: MultiMetaTask
    _live: Live

    def __init__(self, metatasks: MultiMetaTask, refresh_every: float):
        self._live = Live(self._render_request_table(metatasks))
        self._task = asyncio.create_task(self._render_table_loop(metatasks, refresh_every))
        self._metatasks = metatasks

    def stop(self):
        self._live.update(self._render_request_table(self._metatasks))
        self._task.cancel()

    async def _render_table_loop(self, metatasks: MultiMetaTask, refresh_every: float):
        with self._live:
            while True:
                await asyncio.sleep(refresh_every)
                self._live.update(self._render_request_table(metatasks))

    @staticmethod
    def _render_request_table(metatasks: MultiMetaTask) -> Group:
        counts_table = Table(title_style="Request Status", show_edge=True, expand=True)
        counts_table.add_column("Status", style="bold")
        counts_table.add_column("Count", justify="right")
        pending = [metatask for metatask in metatasks if not metatask.task.done()]
        done = [
            metatask
            for metatask in metatasks
            if metatask.task.done() and metatask.task.exception() is None
        ]
        failed = [
            metatask
            for metatask in metatasks
            if metatask.task.done() and metatask.task.exception() is not None
        ]

        counts_table.add_row(
            TaskResult.DONE.value,
            str(len(done)),
        )
        counts_table.add_row(
            TaskResult.PENDING.value,
            str(len(pending)),
        )
        counts_table.add_row(
            TaskResult.FAILED.value,
            str(len(failed)),
        )

        task_table = Table(title="All Tasks", expand=True)
        for title in metatasks.metadata_titles:
            task_table.add_column(title, justify="center")
        task_table.add_column("Status", justify="center")

        for task in pending:
            task_table.add_row(*task.metadata_rows, TaskResult.PENDING.value)
        for task in done:
            task_table.add_row(*task.metadata_rows, TaskResult.DONE.value)
        for task in failed:
            task_table.add_row(*task.metadata_rows, TaskResult.FAILED.value)

        return Group(
            Panel.fit(counts_table, title="Progress"), Panel(task_table, title="URLs")
        )