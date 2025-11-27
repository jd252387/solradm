import os
from pathlib import Path
from typing import List

from . import autocompletion_error
from .utils import _filter_starts_with


def _zk_config_names() -> List[str]:
    from solradm.zk import get_client

    zk = get_client()
    if zk.exists("/configs"):
        return sorted(zk.get_children("/configs"))
    return []


def _default_dir_config_names() -> List[str]:
    from solradm.config.util import get_default_configsets_config_dir

    config_dir = get_default_configsets_config_dir()
    if not config_dir or not config_dir.exists():
        return []
    return sorted(
        [entry.name for entry in config_dir.iterdir() if entry.is_dir()]
    )


def _path_suggestions(incomplete: str) -> List[str]:
    path = Path(incomplete or ".")

    if incomplete.endswith(os.sep) or path.is_dir():
        base_dir = path
        prefix = ""
    else:
        base_dir = path.parent if path.parent != Path("") else Path(".")
        prefix = path.name

    if not base_dir.exists():
        return []

    suggestions: list[str] = []
    for entry in base_dir.iterdir():
        candidate = str(entry)
        if prefix and not entry.name.startswith(prefix):
            continue
        suggestions.append(candidate + (os.sep if entry.is_dir() else ""))
    return suggestions


def config_names(ctx, args: List[str], incomplete: str) -> List[str]:
    try:
        options = _zk_config_names()
    except Exception as e:
        return autocompletion_error(incomplete, e)

    if not options:
        return []
    return _filter_starts_with(options, incomplete)


def config_names_or_paths(ctx, args: List[str], incomplete: str) -> List[str]:
    try:
        options = set(_default_dir_config_names() + _zk_config_names())
        options.update(_path_suggestions(incomplete))
    except Exception as e:
        return autocompletion_error(incomplete, e)

    if not options:
        return []
    return _filter_starts_with(sorted(options), incomplete)
