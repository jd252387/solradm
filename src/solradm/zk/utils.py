import json

from solradm.exceptions.adm_exception import AdmException
from solradm.zk import get_client


def get_overseer_leader() -> str:
    zk_client = get_client()

    if not zk_client.exists("/overseer_elect/leader"):
        raise AdmException("No overseer leader is registered in ZooKeeper!")

    data, stat = get_client().get("/overseer_elect/leader")

    parsed = json.loads(data)
    election : str = parsed["id"]

    return "http://" + election[election.find("-") + 1:election.rfind("_", 0, election.rfind("_", ))]
