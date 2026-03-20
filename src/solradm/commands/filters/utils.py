import functools
import inspect
import re

import rich
import typer
from rich.panel import Panel
from rich.table import Table

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


def _friendly_filter_name(filter_instance) -> str:
    name = filter_instance.__class__.__name__
    if name.endswith("Filter"):
        name = name[:-6]
    pretty = re.sub(r"(?<!^)(?=[A-Z])", " ", name).strip()
    return pretty or filter_instance.__class__.__name__


def with_cluster_state(
    *filter_classes,
    allow_empty: bool = False,
    show_filter_explanations: bool = False,
    skip_fetch_when=None,
):
    """
    Decorator that automatically fetches ClusterState and optionally applies filters.

    Args:
        *filter_classes: Optional filter classes to apply to the cluster state
        allow_empty: Allow decorated command to run even if no collections match
        show_filter_explanations: When True, print a summary of the active filters before executing
        skip_fetch_when: Optional predicate receiving bound kwargs; when truthy, skip fetching cluster state
    """

    def decorator(func):
        orig_sig = inspect.signature(func)
        new_params = [p for p in list(orig_sig.parameters.values()) if p.name != "cluster_state"]

        if filter_classes:
            orig_sig = inspect.signature(func)

            for filter_class in filter_classes:
                for field_name, field_info in filter_class.__dataclass_fields__.items():
                    if "typer_option" not in field_info.metadata:
                        continue

                    typer_option = field_info.metadata.get("typer_option")

                    if any(param.name == field_name for param in new_params):
                        continue

                    new_params.append(inspect.Parameter(
                        field_name,
                        inspect.Parameter.POSITIONAL_OR_KEYWORD,
                        default=typer_option,
                        annotation=field_info.type | None
                    ))

        new_sig = orig_sig.replace(parameters=new_params)
        func.__signature__ = new_sig
        signature = inspect.Signature(parameters=new_params)

        @functools.wraps(func)
        def wrapper(*args, **kwargs):
            filter_instances = []
            skipped_fetch = False

            # caller may provide explicit cluster_state and skip fetching
            # in this case filter arguments are ignored
            if not (cluster_state := kwargs.pop("cluster_state", None)):
                bound_arguments = signature.bind_partial(*args, **kwargs)
                kwargs = dict(bound_arguments.arguments)

                if skip_fetch_when and skip_fetch_when(kwargs):
                    cluster_state = []
                    skipped_fetch = True
                else:
                    try:
                        cluster_state = get_collections()
                    except Exception as e:
                        raise typer.BadParameter(f"Failed to fetch cluster state: {e}")

                    for filter_class in filter_classes:
                        filter_params = {}
                        for field_name in filter_class.__dataclass_fields__:
                            if field_name in kwargs:
                                filter_params[field_name] = kwargs.pop(field_name)

                        filter_instance = filter_class(**filter_params)
                        filter_instance.init()

                        if any(value is not None for value in filter_params.values()):
                            filter_instances.append(filter_instance)

                    for filter_instance in filter_instances:
                        cluster_state = filter_instance.apply(cluster_state)

                if show_filter_explanations and filter_instances:
                    explanation_rows = []
                    for filter_instance in filter_instances:
                        for explanation in filter_instance.describe():
                            if explanation:
                                explanation_rows.append(
                                    (_friendly_filter_name(filter_instance), explanation)
                                )

                    if explanation_rows:
                        table = Table(
                            title="Active filters",
                            header_style="bold magenta",
                            show_lines=False,
                        )

                        table.add_column("Filter", style="cyan", no_wrap=True)
                        table.add_column("Explanation", style="green")

                        for filter_name, explanation in explanation_rows:
                            table.add_row(filter_name, explanation)

                        rich.print(table)

            if len(cluster_state) == 0:
                if (allow_empty and not filter_instances) or skipped_fetch:
                    return func(cluster_state=cluster_state, *args, **kwargs)
                if solradm.api.utils.is_dry_run:
                    rich.print(
                        f"[warning][green bold]💡 EXITING DRY RUN - [/] CLI command \"{func.__qualname__}\" has been called, but the filters didn't match any collections. This may be OK as you are running with dry run, as the previous command did not actually edit the cluster. This command was called with parameters \"{args if args else ''}{kwargs if kwargs else ''}\""
                    )
                else:
                    rich.print("[error] ❌ No collections in the cluster have matched the specified filters!")
                raise typer.Exit(1)

            return func(cluster_state=cluster_state, *args, **kwargs)

        return wrapper

    return decorator
