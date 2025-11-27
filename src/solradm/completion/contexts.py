from typing import List

from . import autocompletion_error
from .utils import _filter_starts_with


def context_names(ctx, args: List[str], incomplete: str) -> List[str]:
    try:
        from solradm.config import settings

        names = [c.name for c in settings.contexts.available]
    except Exception as e:
        return autocompletion_error(incomplete, e)
    return _filter_starts_with(sorted(names), incomplete)


def kube_contexts(ctx, args: List[str], incomplete: str) -> List[str]:
    try:
        from kubernetes.config import list_kube_config_contexts

        contexts, _ = list_kube_config_contexts()
        names = [c["name"] for c in contexts]
    except Exception as e:
        return autocompletion_error(incomplete, e)
    return _filter_starts_with(sorted(names), incomplete)


def context_repo_paths(ctx, args: List[str], incomplete: str) -> List[str]:
    try:
        from solradm.config import settings

        names = []
        for repo in settings.get("context_repositories") or []:
            if isinstance(repo, dict):
                name = repo.get("name")
            else:
                name = getattr(repo, "name", None)

            if name:
                names.append(str(name))
    except Exception as e:
        return autocompletion_error(incomplete, e)
    return _filter_starts_with(sorted(names), incomplete)
