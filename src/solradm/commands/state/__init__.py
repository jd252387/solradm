import asyncio
import json
from pathlib import Path
from typing import List, TYPE_CHECKING

from async_typer import AsyncTyper
import rich
import typer
import yaml
from solradm.lazy import lazy_module

from solradm.zk.utils import get_overseer_leader

api_state = lazy_module("solradm.api.state")
api_utils = lazy_module("solradm.api.utils")

if TYPE_CHECKING:  # pragma: no cover
    from solradm.api.models import Collection

app = AsyncTyper()


@app.command(help="Export cluster state to a file")
def export(file: Path = typer.Argument(..., help="Destination file")) -> None:
    """Serialize the cluster state to a JSON or YAML file."""
    collections = api_state.get_collections()
    data = [c.model_dump() for c in collections]
    if file.suffix.lower() in {".yaml", ".yml"}:
        file.write_text(yaml.safe_dump(data, sort_keys=False))
    else:
        file.write_text(json.dumps(data, indent=2))
    rich.print(f"[success]✅  Exported state to {file}")


@app.async_command(name="import", help="Restore cluster state from a snapshot")
async def import_state(file: Path = typer.Argument(..., help="Snapshot file")) -> None:
    """Read a snapshot and create missing collections/replicas."""
    if not file.exists():
        raise typer.BadParameter(f"Snapshot file {file} does not exist")

    if file.suffix.lower() in {".yaml", ".yml"}:
        data = yaml.safe_load(file.read_text())
    else:
        data = json.loads(file.read_text())

    from solradm.api.models import Collection

    snapshot: List["Collection"] = [Collection.model_validate(c) for c in data]
    current = {c.name: c for c in api_state.get_collections()}
    overseer = get_overseer_leader()

    for coll in snapshot:
        existing_coll = current.get(coll.name)
        if existing_coll is None:
            params = {
                "action": "CREATE",
                "name": coll.name,
                "numShards": len(coll.shards),
                "collection.configName": coll.configName,
                "nrtReplicas": coll.nrtReplicas,
                "tlogReplicas": coll.tlogReplicas,
                "pullReplicas": coll.pullReplicas,
                "replicationFactor": coll.replicationFactor,
                "createNodeSet": "EMPTY",
            }
            await api_utils.send_request(overseer, "/admin/collections", params=params)
            existing_shards = {}
        else:
            existing_shards = {s.name: s for s in existing_coll.shards}

        for shard in coll.shards:
            existing_nodes = set()
            if shard.name in existing_shards:
                existing_nodes = {r.node_name for r in existing_shards[shard.name].replicas}
            for replica in shard.replicas:
                if replica.node_name in existing_nodes:
                    continue
                params = {
                    "action": "ADDREPLICA",
                    "collection": coll.name,
                    "shard": shard.name,
                    "node": replica.node_name,
                    "type": replica.type,
                }
                await api_utils.send_request(overseer, "/admin/collections", params=params)

    rich.print(f"[success]✅  Imported state from {file}")
