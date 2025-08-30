import json
from typing import List

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
    return [get_collection_state(collection) for collection in collection_names]