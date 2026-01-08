from typing import List, Any, TypedDict

from aiohttp import ContentTypeError
from urllib.parse import urljoin, urlparse, urlunparse

import rich
import typer

from solradm.api import get_session
from solradm.api.models import Collection, Replica, Core
from solradm.exceptions.solr_exception import SolrException


class RawResponse(TypedDict):
    ok: bool
    status: int
    data: dict | None
    error_text: str | None


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


async def send_request(
    host: str,
    endpoint: str,
    params: dict = None,
    dry_output: Any | None = None,
    dry_run_override: bool = None,
    method: str = "GET",
    json_body: Any | None = None,
    check_response_header: bool = True,
    return_raw: bool = False,
) -> Any | RawResponse:
    if dry_run_override is not None:
        if dry_run_override:
            return dry_output
    else:
        if is_dry_run:
            return dry_output

    url = urljoin(get_host_with_scheme(host, "http"), "/solr" + endpoint)
    if log_requests:
        rich.print(f"[text]Requesting {method} {url} params={params}")
    try:
        session = get_session()
        if method.upper() == "POST":
            resp = await session.post(url, params=params, json=json_body)
        else:
            resp = await session.get(url, params=params)
    except Exception as e:
        rich.print(f"[error]  ❌ Encountered networking error while sending request: {e}")
        raise e

    try:
        json_data = await resp.json()
    except ContentTypeError:
        if return_raw:
            body = await resp.text()
            return RawResponse(ok=resp.ok, status=resp.status, data=None, error_text=body)
        raise

    if return_raw:
        return RawResponse(ok=resp.ok, status=resp.status, data=json_data, error_text=None)

    if check_response_header:
        if not resp.ok or not json_data["responseHeader"]["status"] == 0:
            rich.print(
                f"[error]❌  Error received from Solr for request to {url} with params {params}:\n[yellow]{json_data['error']['msg']}")
            raise SolrException(json_data["responseHeader"]["status"] == 0, json_data["error"]["msg"])

    return json_data


async def get_cores_from_node(host: str) -> List[Core]:
    json = await send_request(host, "/admin/cores", params={"indexInfo": "false"}, dry_run_override=False)
    return [Core.model_validate(json["status"][key]) for key in json["status"].keys()]
