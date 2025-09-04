from typing import List, Any
from urllib.parse import urljoin, urlparse, urlunparse

import rich
import typer

from solradm.api import get_session
from solradm.api.models import Collection, Replica, Core
from solradm.exceptions.solr_exception import SolrException


def get_collections_using_config(cluster_state: List[Collection], config_name: str) -> List[Collection]:
    return [collection for collection in cluster_state if collection.configName == config_name]


def get_replicas(cluster_state: List[Collection]) -> List[Replica]:
    replicas = []

    for collection in cluster_state:
        for shard in collection.shards:
            for replica in shard.replicas:
                replicas.append(replica)

    return replicas


def validate_num_replicas(replicas: List[Replica]) -> List[Replica]:
    if len(replicas) == 0:
        rich.print("[error]❌ No replicas found!")
        raise typer.Exit(1)

    return replicas


def get_host_with_scheme(host: str, scheme: str) -> str:
    parsed = urlparse(host if "://" in host else f"http://{host}")
    new_parsed = parsed._replace(scheme=scheme)

    return str(urlunparse(new_parsed)).removesuffix("_solr")


is_dry_run = False
log_requests = False


async def send_request(host: str, endpoint: str, params: dict = None, dry_output: Any | None = None,
                       dry_run_override: bool = None) -> Any:
    if dry_run_override is not None:
        if dry_run_override:
            return dry_output
    else:
        if is_dry_run:
            return dry_output

    url = urljoin(get_host_with_scheme(host, "http"), "/solr" + endpoint)
    if log_requests:
        rich.print(f"[text]Requesting {url} params={params}")
    try:
        resp = await get_session().get(url, params=params)
    except Exception as e:
        rich.print(f"[error]  ❌ Encountered networking error while sending request: {e}")
        raise e
    json = await resp.json()
    if not resp.ok or not json["responseHeader"]["status"] == 0:
        rich.print(
            f"[error]❌  Error received from Solr for request to {url} with params {params}:\n[yellow]{json['error']['msg']}")
        raise SolrException(json["responseHeader"]["status"] == 0, json["error"]["msg"])

    return json


async def get_cores_from_node(host: str) -> List[Core]:
    json = await send_request(host, "/admin/cores", params={"indexInfo": "false"}, dry_run_override=False)
    return [Core.model_validate(json["status"][key]) for key in json["status"].keys()]

