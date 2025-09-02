import datetime
from typing import List, Optional

from pydantic import BaseModel, computed_field, field_validator, ConfigDict, Field


class Router(BaseModel):
    name: str
    field: str | None = None

class CoreCloudDescriptor(BaseModel):
    collection: str
    shard: str | None
    replica: str | None
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
    leader: bool = False
    force_set_state: bool
    base_url: str
    shard: Optional['Shard'] = Field(default=None, exclude=True)  # <- kill cycle here

    @computed_field
    @property
    def shard_name(self) -> Optional[str]:
        return self.shard.name if self.shard else None

class Shard(BaseModel):
    name: str
    range: str
    replicas: List[Replica] = Field(default_factory=list)
    collection: Optional['Collection'] = Field(default=None, exclude=True)  # <- and here

    @computed_field
    @property
    def collection_name(self) -> Optional[str]:
        return self.collection.name if self.collection else None

    @field_validator('replicas', mode='before')
    @classmethod
    def transform_replicas_dict_to_list(cls, v):
        if isinstance(v, dict):
            return [Replica(name=replica_name, **replica_data)
                    for replica_name, replica_data in v.items()]
        return v

    def model_post_init(self, __context):
        for r in self.replicas:
            r.shard = self

class Collection(BaseModel):
    name: str
    pullReplicas: int
    configName: str
    replicationFactor: int
    router: 'Router'             # your existing type
    nrtReplicas: int
    tlogReplicas: int
    shards: List[Shard] = Field(default_factory=list)

    @field_validator('shards', mode='before')
    @classmethod
    def transform_shards_dict_to_list(cls, v):
        if isinstance(v, dict):
            return [Shard(name=shard_name, **shard_data)
                    for shard_name, shard_data in v.items()]
        return v

    def model_post_init(self, __context):
        for s in self.shards:
            s.collection = self