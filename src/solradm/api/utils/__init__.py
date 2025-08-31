from typing import List
from urllib.parse import urljoin
import rich
from solradm.api import get_session
from solradm.api.models import Collection, Replica
from solradm.exceptions.solr_exception import SolrException


def get_replicas(cluster_state: List[Collection]) -> List[Replica]:
    replicas = []

    for collection in cluster_state:
        for shard in collection.shards:
            for replica in shard.replicas:
                replicas.append(replica)

    return replicas


async def send_request(host: str, endpoint: str, params: dict = None):
    url = urljoin(host, "/solr" + endpoint)
    resp = await get_session().get(url,
                                   params=params)
    json = await resp.json()
    if not resp.ok or not json["responseHeader"]["status"] == 0:
        rich.print(f"[error]❌  Error received from Solr for request to {url}:\n{json["error"]["msg"]}")
        raise SolrException(json["responseHeader"]["status"] == 0, json["error"]["msg"])
