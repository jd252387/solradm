from pathlib import Path
from solradm.config import settings
from solradm.config.context import Context


def get_current_context() -> Context:
    current = settings.contexts.current

    if "name" in current:
        context_dict = next((context for context in settings.contexts.available if context.name == settings.contexts.current.name), None)

        return Context(context_dict.name, context_dict.zk, context_dict.get("kubecontext"))
    else:
        return Context(None, current.zk, current.get("kubecontext"))


def get_default_config_dir() -> Path | None:
    path = settings.get("config_dir")
    return Path(path) if path else None


def validate_config_dir(path: Path) -> bool:
    return path.is_dir() and (path / "root").is_dir() and (path / "configsets").is_dir()
