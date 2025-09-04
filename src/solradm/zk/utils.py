import json
import os

from solradm.exceptions.adm_exception import AdmException
from solradm.zk import get_client

def win_path_to_zk_path(win_path, znode_root = "/"):
    zk_path = win_path.replace("\\", "/")

    if ":" in zk_path:
        zk_path = zk_path.split(":", 1)[1]

    if not zk_path.startswith("/"):
        zk_path = "/" + zk_path

    zk_path = os.path.join(znode_root, zk_path.lstrip("/"))
    zk_path = zk_path.replace("//", "/")

    return zk_path

def get_overseer_leader() -> str:
    zk_client = get_client()

    if not zk_client.exists("/overseer_elect/leader"):
        raise AdmException("No overseer leader is registered in ZooKeeper!")

    data, stat = get_client().get("/overseer_elect/leader")

    parsed = json.loads(data)
    election: str = parsed["id"]

    return "http://" + election[election.find("-") + 1:election.rfind("_", 0, election.rfind("_", ))]
