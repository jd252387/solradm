import re
from dataclasses import dataclass, field
from typing import List, Optional, TYPE_CHECKING

import typer

from solradm import completion
from solradm.commands.filters.filter import Filter

if TYPE_CHECKING:  # pragma: no cover
    pass


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
                autocompletion=completion.shard_numbers,
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
                autocompletion=completion.shard_numbers,
            )
        },
    )

    def init(self):
        # nothing required on init
        pass

    def _parse_spec(self, spec: str):
        rules = []
        for part in spec.split(","):
            part = part.strip()
            if not part:
                continue
            seq_match = re.fullmatch(r"(?:(\d+)?\+(\d+)(?:-(\d+))?)", part)
            if seq_match:
                start = int(seq_match.group(1)) if seq_match.group(1) else 1
                step = int(seq_match.group(2))
                end = int(seq_match.group(3)) if seq_match.group(3) else None
                rules.append(("seq", start, step, end))
                continue
            range_match = re.fullmatch(r"(\d+)-(\d+)", part)
            if range_match:
                rules.append(("range", int(range_match.group(1)), int(range_match.group(2))))
                continue
            if part.isdigit():
                rules.append(("eq", int(part)))
                continue
            raise typer.BadParameter(f"Invalid shard specification '{part}'")
        return rules

    def _matches(self, rules, shard_num: int) -> bool:
        for rule in rules:
            kind = rule[0]
            if kind == "eq" and shard_num == rule[1]:
                return True
            if kind == "range" and rule[1] <= shard_num <= rule[2]:
                return True
            if kind == "seq":
                start, step, end = rule[1], rule[2], rule[3]
                if shard_num >= start and (shard_num - start) % step == 0:
                    if end is None or shard_num <= end:
                        return True
        return False

    def apply(self, cluster_state: List["Collection"]) -> List["Collection"]:
        include_rules = self._parse_spec(self.shards) if self.shards else []
        exclude_rules = self._parse_spec(self.exclude_shards) if self.exclude_shards else []

        filtered_collections = []
        for collection in cluster_state:
            new_shards = []
            for shard in collection.shards:
                match_include = (
                    self._matches(include_rules, int(re.findall(r"\d+", shard.name)[0]))
                    if include_rules
                    else True
                )
                match_exclude = (
                    self._matches(exclude_rules, int(re.findall(r"\d+", shard.name)[0]))
                    if exclude_rules
                    else False
                )
                if match_include and not match_exclude:
                    new_shards.append(shard)
            if new_shards:
                collection.shards = new_shards
                filtered_collections.append(collection)
        return filtered_collections
