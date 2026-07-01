"""``sa rt setup`` — (re)configure and persist the Artifactory profile."""

import rich

from solradm.commands.rt.subapp import app
from solradm.config import persist, settings
from solradm.config.interactive import setup_artifactory


@app.command(help="Configure and persist your Artifactory profile (url, repos, access token).")
def setup():
    """Re-run the interactive Artifactory setup and store it in solradm's config."""
    settings.set("artifactory", setup_artifactory.setup())
    persist()
    rich.print("[success]✅  Saved Artifactory profile!")
