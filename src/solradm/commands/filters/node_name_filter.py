import re
from dataclasses import dataclass, field
from typing import List, Optional

import rich
import typer

from solradm.api.models import Collection
from solradm.commands.filters.filter import Filter
from solradm.completion.nodes import node_names


@dataclass
class NodeNameFilter(Filter):
    """Filter replicas by node name using include and exclude regexes."""

    node: Optional[List[str]] = field(
        default=None,
        metadata={
            "typer_option": typer.Option(
                None,
                "--node",
                help="Regex to select nodes",
                autocompletion=node_names,
            )
        },
    )

    exclude_node: Optional[List[str]] = field(
        default=None,
        metadata={
            "typer_option": typer.Option(
                None,
                "--exclude-node",
                help="Regex to exclude nodes",
                autocompletion=node_names,
            )
        },
    )

    _include_regexes: list[re.Pattern[str]] = field(init=False, default_factory=list)
    _exclude_regexes: list[re.Pattern[str]] = field(init=False, default_factory=list)

    def init(self):
        self._include_regexes = self._compile_patterns(self.node, "--node")
        self._exclude_regexes = self._compile_patterns(self.exclude_node, "--exclude-node")

    def _compile_patterns(
        self, patterns: Optional[List[str]], option_display: str
    ) -> list[re.Pattern[str]]:
        compiled: list[re.Pattern[str]] = []
        for pattern in patterns or []:
            try:
                compiled.append(re.compile(pattern))
            except re.error as exc:
                raise typer.BadParameter(
                    f"Invalid regular expression for {option_display} '{pattern}': {exc}"
                ) from exc
        return compiled

    def _matches(self, node_name: str) -> bool:
        if self._include_regexes and not any(
            regex.search(node_name) for regex in self._include_regexes
        ):
            return False
        if self._exclude_regexes and any(
            regex.search(node_name) for regex in self._exclude_regexes
        ):
            return False
        return True

    def apply(self, cluster_state: List[Collection]) -> List[Collection]:
        if not self._include_regexes and not self._exclude_regexes:
            return cluster_state

        replicas = [
            replica
            for coll in cluster_state
            for shard in coll.shards
            for replica in shard.replicas
            if replica.node_name
        ]

        selected_nodes = sorted({
            replica.node_name for replica in replicas if self._matches(replica.node_name)
        })

        if not selected_nodes:
            rich.print("[error] ❌ No nodes match the given selectors")
            raise typer.Exit(1)

        filtered_collections: list[Collection] = []
        filtered_replicas = []
        for coll in cluster_state:
            new_shards = []
            for shard in coll.shards:
                new_replicas = [
                    replica for replica in shard.replicas if replica.node_name in selected_nodes
                ]
                if new_replicas:
                    shard.replicas = new_replicas
                    new_shards.append(shard)
                    filtered_replicas.extend(new_replicas)
            if new_shards:
                coll.shards = new_shards
                filtered_collections.append(coll)

        if not filtered_replicas:
            rich.print("[error] ❌ No replicas match the given node selectors")
            raise typer.Exit(1)

        return filtered_collections

    def describe(self) -> List[str]:
        descriptions: List[str] = []
        if self.node:
            descriptions.append(
                "Replicas on nodes matching include patterns: "
                + ", ".join(self.node)
            )
        if self.exclude_node:
            descriptions.append(
                "Replicas excluding nodes that match: " + ", ".join(self.exclude_node)
            )
        return descriptions
