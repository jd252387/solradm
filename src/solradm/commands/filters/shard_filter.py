import re
from dataclasses import field, dataclass
from typing import List, Optional

import typer

from solradm.api.models import Collection
from solradm.commands.filters.filter import Filter
from solradm.commands.filters.shard_utils import (
    matches_shard_number,
    parse_shard_spec,
)
from solradm.completion.collections import shard_numbers


@dataclass
class ShardFilter(Filter):
    """Filter shards by shard number specification."""
    shards: Optional[str] = field(
        default=None,
        metadata={
            "typer_option": typer.Option(
                None,
                "--shards",
                help="Shard numbers to include (e.g. '1,3-5,2+3-7,+4-16')",
                autocompletion=shard_numbers,
            )
        },
    )
    exclude_shards: Optional[str] = field(
        default=None,
        metadata={
            "typer_option": typer.Option(
                None,
                "--exclude-shards",
                help="Shard numbers to exclude",
                autocompletion=shard_numbers,
            )
        },
    )

    def init(self):
        # nothing required on init
        pass

    def apply(self, cluster_state: List[Collection]) -> List[Collection]:
        include_rules = parse_shard_spec(self.shards) if self.shards else []
        exclude_rules = parse_shard_spec(self.exclude_shards) if self.exclude_shards else []

        filtered_collections = []
        for collection in cluster_state:
            new_shards = []
            for shard in collection.shards:
                match_include = (
                    matches_shard_number(
                        include_rules, int(re.findall(r"\d+", shard.name)[0])
                    )
                    if include_rules
                    else True
                )
                match_exclude = (
                    matches_shard_number(
                        exclude_rules, int(re.findall(r"\d+", shard.name)[0])
                    )
                    if exclude_rules
                    else False
                )
                if match_include and not match_exclude:
                    new_shards.append(shard)
            if new_shards:
                collection.shards = new_shards
                filtered_collections.append(collection)
        return filtered_collections

    def describe(self) -> List[str]:
        descriptions: List[str] = []
        if self.shards:
            descriptions.append(
                f"Include shards whose numeric identifier matches '{self.shards}'"
            )
        if self.exclude_shards:
            descriptions.append(
                f"Exclude shards whose numeric identifier matches '{self.exclude_shards}'"
            )
        return descriptions
