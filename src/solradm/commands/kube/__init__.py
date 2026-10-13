# Import order sets the order commands appear in `sa kube --help`, so these are
# listed in that order rather than alphabetically.
from . import (
    logs,
    disk,
    suspend,
    resume,
    state_dir,
    ui,
    list_resources,
)
from .subapp import app
