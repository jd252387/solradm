import functools
import inspect

import rich
import typer
from rich.panel import Panel

import solradm.api.utils
from solradm.api.state import get_collections


def with_dry_run(func):
    orig_sig = inspect.signature(func)
    new_sig = orig_sig.replace(parameters=list(orig_sig.parameters.values()) + [
        inspect.Parameter("dry_run", inspect.Parameter.POSITIONAL_OR_KEYWORD,
                          default=typer.Option(False, "--dry", "-d", help="Dry run"), annotation=bool)])
    func.__signature__ = new_sig

    @functools.wraps(func)
    def wrapper(dry_run, *args, **kwargs):
        if dry_run:
            solradm.api.utils.is_dry_run = True
            rich.print(Panel("Dry Run", style="bold green"))

        return func(*args, **kwargs)

    return wrapper


def with_cluster_state(*filter_classes):
    """
    Decorator that automatically fetches ClusterState and optionally applies filters.

    Args:
        *filter_classes: Optional filter classes to apply to the cluster state
    """

    def decorator(func):
        orig_sig = inspect.signature(func)
        new_params = [p for p in list(orig_sig.parameters.values()) if p.name != "cluster_state"]

        if filter_classes:
            orig_sig = inspect.signature(func)

            for filter_class in filter_classes:
                for field_name, field_info in filter_class.__dataclass_fields__.items():
                    typer_option = field_info.metadata.get("typer_option")

                    new_params.append(inspect.Parameter(
                        field_name,
                        inspect.Parameter.POSITIONAL_OR_KEYWORD,
                        default=typer_option,
                        annotation=field_info.type | None
                    ))

        new_sig = orig_sig.replace(parameters=new_params)

        func.__signature__ = new_sig

        @functools.wraps(func)
        def wrapper(*args, **kwargs):

            filter_instances = []
            for filter_class in filter_classes:
                filter_params = {}
                for field_name in filter_class.__dataclass_fields__:
                    if field_name in kwargs:
                        filter_params[field_name] = kwargs.pop(field_name)

                filter_instance = filter_class(**filter_params)
                filter_instance.init()

                if any(filter_params.values()):
                    filter_instances.append(filter_instance)
            try:
                cluster_state = get_collections()
            except Exception as e:
                raise typer.BadParameter(f"Failed to fetch cluster state: {e}")

            for filter_instance in filter_instances:
                cluster_state = filter_instance.apply(cluster_state)

            return func(cluster_state=cluster_state, *args, **kwargs)

        return wrapper

    return decorator
