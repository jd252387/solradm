from typing import Iterable, List

from solradm.tasks.metatask import MetaTask


class MultiMetaTask(Iterable[MetaTask]):
    tasks: List[MetaTask]
    metadata_titles: List[str]

    def __init__(self, metadata_titles: List[str], tasks: List[MetaTask]):
        for i, task in enumerate(tasks):
            if len(task.metadata_rows) != len(metadata_titles):
                raise ValueError(
                    f"Task {task} at index {i} does not match the number of given metadata_titles"
                )

        self.tasks = tasks
        self.metadata_titles = metadata_titles

    def add_tasks(self, tasks: Iterable[MetaTask]):
        for i, task in enumerate(tasks):
            if len(task.metadata_rows) != len(self.metadata_titles):
                raise ValueError(
                    f"Task {task} at index {i} does not match the number of given metadata_titles"
                )
            self.tasks.append(task)

    def __iter__(self):
        task_index = 0
        while task_index < len(self.tasks):
            yield self.tasks[task_index]
            task_index += 1
