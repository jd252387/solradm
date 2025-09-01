import json
from pathlib import Path
from typing import Tuple

import rich
import typer
from async_typer import AsyncTyper
import yaml

from solradm.api.models import Collection
from solradm.api.state import get_collections
from solradm.api.utils import send_request
from solradm.commands.filters.utils import with_dry_run
from solradm.zk.utils import get_overseer_leader

app = AsyncTyper()


def _collection_dump(collection: Collection) -> dict:
    """Serialize a Collection model removing circular references."""
    return collection.model_dump(exclude={
        "shards": {
            "__all__": {
                "collection": True,
                "replicas": {"__all__": {"shard": True}}
            }
        }
    })


@app.command()
def export(path: Path = typer.Argument(..., help="Output snapshot file", dir_okay=False)):
    """Export cluster state to a JSON or YAML file."""
    collections = get_collections()
    data = [_collection_dump(c) for c in collections]

    if path.suffix.lower() in {".yml", ".yaml"}:
        path.write_text(yaml.safe_dump(data, sort_keys=False))
    else:
        path.write_text(json.dumps(data, indent=2))

    rich.print(f"[success]✅  Exported state to {path}")


@app.async_command(name="import", help="Restore cluster state from a snapshot")
@with_dry_run
async def import_state(snapshot: Path = typer.Argument(..., exists=True, dir_okay=False)):
    """Import cluster state from a snapshot file."""
    if snapshot.suffix.lower() in {".yml", ".yaml"}:
        data = yaml.safe_load(snapshot.read_text())
    else:
        data = json.loads(snapshot.read_text())

    target_cols = [Collection.model_validate(c) for c in data]
    existing_cols = {c.name: c for c in get_collections()}

    overseer = get_overseer_leader()

    for coll in target_cols:
        existing = existing_cols.get(coll.name)
        if existing is None:
            params = {
                "action": "CREATE",
                "name": coll.name,
                "numShards": len(coll.shards),
                "collection.configName": coll.configName,
                "createNodeSet": "EMPTY",
                "tlogReplicas": coll.tlogReplicas,
                "nrtReplicas": coll.nrtReplicas,
                "pullReplicas": coll.pullReplicas,
                "router.name": coll.router.name,
            }
            if coll.router.field:
                params["router.field"] = coll.router.field
            await send_request(overseer, "/admin/collections", params=params)
            rich.print(f"[success]✅  Created collection {coll.name}")
            existing_shards = {}
        else:
            existing_shards = {s.name: s for s in existing.shards}

        for shard in coll.shards:
            existing_replicas: set[Tuple[str, str]] = set()
            if shard.name in existing_shards:
                existing_replicas = {(r.node_name, r.type) for r in existing_shards[shard.name].replicas}
            for replica in shard.replicas:
                key = (replica.node_name, replica.type)
                if key not in existing_replicas:
                    await send_request(
                        overseer,
                        "/admin/collections",
                        params={
                            "action": "ADDREPLICA",
                            "collection": coll.name,
                            "shard": shard.name,
                            "node": replica.node_name,
                            "type": replica.type,
                        },
                    )
                    rich.print(
                        f"[success]✅  Added replica for {coll.name}/{shard.name} on {replica.node_name}"
                    )
