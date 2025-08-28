from asyncio import Task
from typing import List


class MetaTask:
    metadata: List[str]
    task: Task

    def __init__(self, metadata: List[str], awaitable: Task):
        self.metadata_rows = metadata
        self.task = awaitable
