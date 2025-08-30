from typing import List
from urllib.parse import urljoin

from solradm.api import get_session
from solradm.api.core.core import CoreDescriptor
from solradm.zk.utils import get_overseer_leader


async def get_cores(collection: str = None) -> List[CoreDescriptor]:
    response = await get_session().get(url=urljoin(get_overseer_leader(), "/solr/admin/collections"),
                          params={"action": "CLUSTERSTATUS"})
    
    json = await response.json()
    if collection in json["cluster"]["collections"].keys():
        cores = []
        for shard in json["cluster"]["collections"][collection]["shards"].values():
            for replica in shard["replicas"].values():
                cores.append(CoreDescriptor(replica["base_url"], replica["core"]))
        return cores
    else:
        raise Exception(f"Collection \"{collection}\" was not found!")