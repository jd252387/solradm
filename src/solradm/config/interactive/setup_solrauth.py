from aiohttp import BasicAuth
from rich.prompt import Prompt


def setup() -> BasicAuth:
    username = ""
    while username == "":
        username = Prompt.ask("[question]Enter your Solr username -> ")
    password = ""
    while password == "":
        password = Prompt.ask("[question]Enter your Solr password -> ")

    return BasicAuth(username, password)