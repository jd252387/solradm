import importlib
from typing import Dict, Tuple

import click
from typer.models import CommandInfo
import typer.main as typer_main


class LazyGroup(click.Group):
    """A click Group that lazy-loads Typer apps and commands."""

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self._lazy: Dict[str, Tuple[str, str, str, str | None]] = {}
        # mapping name -> (kind, import_path, attr, help)
        # kind: 'typer' or 'command'

    def add_lazy_typer(self, import_path: str, name: str, help: str | None = None) -> None:
        """Register a Typer app to be lazily imported."""
        module, attr = import_path.split(":")
        self._lazy[name] = ("typer", module, attr, help)

    def add_lazy_command(self, import_path: str, name: str, help: str | None = None) -> None:
        """Register a command callback to be lazily imported."""
        module, attr = import_path.split(":")
        self._lazy[name] = ("command", module, attr, help)

    def list_commands(self, ctx: click.Context) -> list[str]:
        names = set(super().list_commands(ctx))
        names.update(self._lazy.keys())
        return sorted(names)

    def get_command(self, ctx: click.Context, name: str):
        if name in self._lazy:
            kind, module, attr, help_text = self._lazy[name]
            if ctx and ctx.resilient_parsing:
                return click.Command(name, help=help_text)
            mod = importlib.import_module(module)
            target = getattr(mod, attr)
            if kind == "typer":
                cmd = typer_main.get_command(target)
            else:
                info = CommandInfo(name=name, callback=target, help=help_text)
                cmd = typer_main.get_command_from_info(
                    info,
                    pretty_exceptions_short=False,
                    rich_markup_mode=None,
                )
            self.add_command(cmd, name)
            return cmd
        return super().get_command(ctx, name)
