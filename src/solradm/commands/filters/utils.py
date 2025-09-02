import functools
import inspect

import typer

from solradm.lazy import lazy_module

rich = lazy_module("rich")
Panel = lazy_module("rich.panel").Panel
api_utils = lazy_module("solradm.api.utils")
api_state = lazy_module("solradm.api.state")


def with_dry_run(func):
    orig_sig = inspect.signature(func)
    new_sig = orig_sig.replace(parameters=list(orig_sig.parameters.values()) + [
        inspect.Parameter("dry_run", inspect.Parameter.POSITIONAL_OR_KEYWORD,
                          default=typer.Option(False, "--dry", "-d", help="Dry run"), annotation=bool)])
    func.__signature__ = new_sig

    @functools.wraps(func)
    def wrapper(dry_run, *args, **kwargs):
        if dry_run:
            api_utils.is_dry_run = True
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
                cluster_state = api_state.get_collections()
            except Exception as e:
                raise typer.BadParameter(f"Failed to fetch cluster state: {e}")

            for filter_instance in filter_instances:
                cluster_state = filter_instance.apply(cluster_state)

            if len(cluster_state) == 0:
                if api_utils.is_dry_run:
                    rich.print(f"[warning][green bold]💡 EXITING DRY RUN - [/] CLI command \"{func.__qualname__}\" has been called, but the filters didn't match any collections. This may be OK as you are running with dry run, as the previous command did not actually edit the cluster. This command was called with parameters \"{args if args else ""}{kwargs if kwargs else ""}")
                else:
                    rich.print("[error] ❌ No collections in the cluster have matched the specified filters!")
                raise typer.Exit(1)

            return func(cluster_state=cluster_state, *args, **kwargs)

        return wrapper

    return decorator
