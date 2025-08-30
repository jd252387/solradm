from dataclasses import dataclass

from solradm.api import get_session
from solradm.exceptions.solr_exception import SolrException


@dataclass
class CoreDescriptor:
    base_url: str
    core_name: str


async def reload_core(core_descriptor: CoreDescriptor):
    resp = await get_session().get(core_descriptor.base_url + "/admin/cores",
                            params={"action": "RELOAD", "core": core_descriptor.core_name})
    json = await resp.json()
    if not resp.ok or not json["responseHeader"]["status"] == 0:
        raise SolrException(
            f"{core_descriptor.core_name} in {core_descriptor.base_url} failed to reload with error: {json}")
