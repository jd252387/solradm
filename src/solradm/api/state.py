import json
from typing import List, Literal, Dict

from kazoo.exceptions import NoNodeError
from pydantic import ValidationError

from solradm.api.models import Collection
from solradm.zk import get_client


def get_collection_names() -> List[str]:
    zk = get_client()
    collections = zk.get_children("/collections")
    return collections


def get_collection_state(collection: str) -> Collection:
    zk = get_client()
    collection_path = f"/collections/{collection}/state.json"
    data, stat = zk.get(collection_path)
    state = json.loads(data.decode("utf-8"))[collection]
    # Add the collection name to the state data
    state["name"] = collection
    return Collection.model_validate(state)


def get_collections() -> List[Collection]:
    collection_names = get_collection_names()
    collections = []
    for collection in collection_names:
        try:
            collections.append(get_collection_state(collection))
        except (NoNodeError, json.JSONDecodeError, UnicodeDecodeError, KeyError, TypeError, ValidationError):
            # Some znodes under /collections may not contain a usable state.json.
            # Ignore those and continue processing valid collections.
            continue
    return collections


def get_aliases() -> Dict[str, List[str]]:
    """Map each collection name to the aliases pointing at it.

    Solr stores /aliases.json as alias -> comma-separated collections; invert it.
    """
    try:
        data, stat = get_client().get("/aliases.json")
    except NoNodeError:
        return {}

    # Solr creates /aliases.json empty and only writes to it once an alias exists,
    # so on a cluster with no aliases the znode is present but kazoo returns None.
    if not data:
        return {}

    aliases: Dict[str, List[str]] = {}
    for alias, targets in json.loads(data.decode("utf-8")).get("collection", {}).items():
        for target in targets.split(","):
            aliases.setdefault(target.strip(), []).append(alias)
    return aliases


def get_nodes_by_role(role: Literal["coordinator", "data", "overseer"]) -> Dict[str, str]:
    states = get_client().get_children(f"/node_roles/{role}")
    state_to_node = {}
    for state in states:
        state_to_node[state] = get_client().get_children(f"/node_roles/{role}/{state}")

    return state_to_node
