import re
from dataclasses import field, dataclass
from typing import Optional, List

import typer
from rich.prompt import Confirm

from solradm import completion
from solradm.api.models import Collection
from solradm.commands.filters.filter import Filter


@dataclass
class CollectionNameFilter(Filter):
    """Filter collections by name using regex."""
    collection_name_filter: Optional[str] = field(
        default=None,
        metadata={
            "typer_option": typer.Option(
                None,
                "--collection",
                help="Regex pattern to filter collections by name",
                autocompletion=completion.collection_names,
            )
        }
    )

    skip_checks: Optional[bool] = field(
        default=False,
        metadata={
            "typer_option": typer.Option(False, "--skip-checks", "-y", help="Skip filter absence checks")
        }
    )

    def init(self):
        if self.collection_name_filter is None and not self.skip_checks:
            if not Confirm.ask(
                    "No collection filter was specified, so this command will run across all collections, adhering to any other filters you have placed.\nAre you sure you want to continue?"):
                raise typer.Exit(0)

    def apply(self, cluster_state: List[Collection]) -> List[Collection]:
        if self.collection_name_filter is not None:
            try:
                pattern = re.compile(self.collection_name_filter)
                return [
                    c for c in cluster_state
                    if pattern.search(c.name)
                ]
            except re.error as e:
                raise typer.BadParameter(f"Invalid regex pattern '{self.collection_name_filter}': {e}")
        else:
            return cluster_state