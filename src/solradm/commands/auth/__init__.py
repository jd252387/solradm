import subprocess
import sys

import rich
from typer import Typer

from solradm.config import settings, persist, config_path
from solradm.config.interactive import setup_solrauth

app = Typer()


@app.command()
def edit():
    """Edit stored Solr credentials"""
    auth = setup_solrauth.setup()
    settings.set("auth", {"user": auth.login, "password": auth.password})
    persist()
    rich.print("[success]✅  Updated Solr credentials!")


@app.command("open-config")
def open_config():
    """Open the configuration directory and highlight the settings file"""
    if sys.platform.startswith("win"):
        subprocess.run(["explorer", f"/select,{config_path}"])
    elif sys.platform == "darwin":
        subprocess.run(["open", "-R", str(config_path)])
    else:
        subprocess.run(["xdg-open", str(config_path.parent)])
