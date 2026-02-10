import json
from typing import List, Literal, Dict

from kazoo.exceptions import NoNodeError

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
        except NoNodeError:
            # Some znodes under /collections may not have a state.json child.
            # Ignore those and continue processing valid collections.
            continue
    return collections


def get_nodes_by_role(role: Literal["coordinator", "data", "overseer"]) -> Dict[str, str]:
    states = get_client().get_children(f"/node_roles/{role}")
    state_to_node = {}
    for state in states:
        state_to_node[state] = get_client().get_children(f"/node_roles/{role}/{state}")

    return state_to_node
