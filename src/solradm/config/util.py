import os
import os
from pathlib import Path

import typer
from solradm.lazy import lazy_module

from solradm.config import settings
from solradm.config.context import Context

rich = lazy_module("rich")


def get_current_context() -> Context:
    current = settings.contexts.current

    if "name" in current:
        context_dict = next((context for context in settings.contexts.available if context.name == settings.contexts.current.name), None)

        return Context(context_dict.name, context_dict.zk, context_dict.get("kubecontext"))
    else:
        return Context(None, current.zk, current.get("kubecontext"))


def _get_default_znode_dir() -> Path | None:
    path = settings.get("config_dir")
    return Path(path) if path else None

def get_default_configsets_config_dir() -> Path | None:
    return _get_default_znode_dir() / "configsets"

def get_default_configsets_root_dir() -> Path | None:
    return _get_default_znode_dir() / "root"

def resolve_config_name_to_abs_or_default_directory(path: Path) -> Path | None:
    if not os.path.isabs(path):
        config_dir = get_default_configsets_config_dir()
        path = config_dir / path

    if not path.exists():
        rich.print(f"[error]❌ Path {path} does not exist!")
        raise typer.Exit(1)

    return path

def _validate_config_dir(path: Path) -> bool:
    return path.is_dir() and (path / "root").is_dir() and (path / "configsets").is_dir()

def validate_config_dir(path: Path):
    if not _validate_config_dir(path):
        rich.print(f"[error]❌ Used a relative path to the default configuration directory, but it is not configured or invalid. Use sa context config-dir to modify fix this.")
        raise typer.Exit(1)
