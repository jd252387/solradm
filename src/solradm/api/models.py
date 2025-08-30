from typing import Literal, List
from pydantic import BaseModel, field_validator

class Router(BaseModel):
    name: str
    field: str | None

class Node(BaseModel):
    name: str
    coordinator_role: bool
    data_role: bool
    overseer_role: Literal["allowed", "disallowed", "preferred"]

class Replica(BaseModel):
    name: str
    core: str
    node_name: str
    type: str
    state: str
    leader: bool
    force_set_state: bool
    base_url: str

class Shard(BaseModel):
    name: str
    range: str
    replicas: List[Replica]
    
    @field_validator('replicas', mode='before')
    @classmethod
    def transform_replicas_dict_to_list(cls, v):
        if isinstance(v, dict):
            # Transform dict of replicas to list, setting name from key
            return [
                Replica(name=replica_name, **replica_data)
                for replica_name, replica_data in v.items()
            ]
        return v

class Collection(BaseModel):
    name: str
    pullReplicas: int
    configName: str
    replicationFactor: int
    router: Router
    nrtReplicas: int
    tlogReplicas: int
    shards: List[Shard]
    
    @field_validator('shards', mode='before')
    @classmethod
    def transform_shards_dict_to_list(cls, v):
        if isinstance(v, dict):
            # Transform dict of shards to list, setting name from key
            return [
                Shard(name=shard_name, **shard_data)
                for shard_name, shard_data in v.items()
            ]
        return v