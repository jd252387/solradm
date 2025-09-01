from pathlib import Path

from solradm.config import settings
from solradm.config.context import Context


def get_current_context() -> Context:
    current = settings.contexts.current
    if "name" in current:
        context_dict = next(
            (
                context
                for context in settings.contexts.available
                if context.name == settings.contexts.current.name
            ),
            None,
        )
        return Context(
            context_dict.name,
            context_dict.zk,
            context_dict.get("kubecontext"),
        )
    return Context(None, current.zk, current.get("kubecontext"))


def is_valid_config_dir(path: Path) -> bool:
    return (
        path.is_dir()
        and (path / "root").is_dir()
        and (path / "configsets").is_dir()
    )


def get_configsets_dir() -> Path:
    base = Path(settings.local_config_dir)
    if not is_valid_config_dir(base):
        raise FileNotFoundError("Invalid configuration directory")
    return base / "configsets"


def resolve_config_path(value: str) -> Path:
    candidate = Path(value)
    if candidate.exists():
        return candidate
    candidate = get_configsets_dir() / value
    if candidate.exists():
        return candidate
    raise FileNotFoundError(f'Configuration "{value}" not found')
