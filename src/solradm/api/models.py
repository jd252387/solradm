import datetime
from typing import Literal, List
from pydantic import BaseModel, field_validator, ConfigDict, Field


class Router(BaseModel):
    name: str
    field: str | None

class CoreCloudDescriptor(BaseModel):
    collection: str
    shard: str
    replica: str
    replicaType: str

class Core(BaseModel):
    name: str
    instanceDir: str
    dataDir: str
    config: str
    schema_: str = Field(alias="schema")
    startTime: datetime.datetime
    uptime: int
    lastPublished: str
    configVersion: int
    cloud: CoreCloudDescriptor
    # index information may come in the core json
    model_config = ConfigDict(extra="ignore")

class Replica(BaseModel):
    name: str
    core: str
    node_name: str
    type: str
    state: str
    leader: bool
    force_set_state: bool
    base_url: str
    shard: 'Shard | None' = None

class Shard(BaseModel):
    name: str
    range: str
    replicas: List[Replica]
    collection: 'Collection | None' = None
    
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

    def model_post_init(self, __context):
        for replica in self.replicas:
            replica.shard = self

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

    def model_post_init(self, __context):
        for shard in self.shards:
            shard.collection = self