import asyncio
import difflib
import re
import shutil
import subprocess
import tempfile
import time
from pathlib import Path
from typing import List

from jinja2 import ChoiceLoader, Environment, FileSystemLoader

from typer.models import OptionInfo

import rich
import typer
from rich.panel import Panel
from rich.prompt import Confirm
from rich.table import Table
from rich.text import Text
from watchdog.observers import Observer

from solradm.api import get_initialized_session
from solradm.api.models import Collection
from solradm.api.state import get_collections
from solradm.api.utils import get_collections_using_config
from solradm.commands.callbacks import add_verbosity_option
from solradm.commands.filters.collection_name_filter import CollectionNameFilter
from solradm.commands.filters.utils import with_cluster_state
from solradm.commands.zk.utils import (
    open_vscode,
    collect_znode_files,
    create_or_update,
    build_files_by_config,
    compile_regex_patterns,
    iter_local_files,
)
from solradm.commands.zk.utils.sync_handler import ZooKeeperSyncHandler
from solradm.commands.zk.utils.znode_copier import copy_znode_to_local
from solradm.completion.collections import collection_names
from solradm.completion.configs import config_names_or_paths
from solradm.completion.znodes import znode_paths
from solradm.config.util import (
    get_default_configsets_config_dir,
    resolve_config_name_to_abs_or_default_directory,
)
from solradm.exceptions.adm_exception import AdmException
from solradm.zk import get_client
from solradm.zk.utils import win_path_to_zk_path

app = typer.Typer()
add_verbosity_option(app)


def _coerce_optional_option(value):
    return value.default if isinstance(value, OptionInfo) else value


def _open_znode_session(
        znode_path: str,
        *,
        sync_interval: int,
        reload: bool,
        read_only: bool,
) -> None:
    header_text = "ZNode Viewer" if read_only else "ZNode Copier & Sync Tool"
    panel_title = "👀 ZooKeeper Viewer" if read_only else "🚀 ZooKeeper Integration"

    rich.print(
        Panel.fit(
            Text(header_text),
            title=panel_title,
        )
    )

    with tempfile.TemporaryDirectory() as temp_dir:
        rich.print(f"[blue]📁 Created temporary directory: {temp_dir}")

        try:
            rich.print(f"[blue]📋 Copying zNode {znode_path} to temporary directory...")
            if not copy_znode_to_local(
                    zk=get_client(),
                    znode_path=znode_path,
                    local_dir=temp_dir,
                    include_data=True,
            ):
                raise typer.Exit(1)

            vscode_process = None
            try:
                vscode_process = open_vscode(temp_dir)
            except AdmException:
                rich.print(
                    "[error]❌ Failed to open VSCode. Make sure 'code' command is available in PATH"
                )
                raise typer.Exit(1)

            observer = None
            event_handler = None

            if read_only:
                rich.print("[yellow]💡 Viewing mode: changes made locally will NOT sync to ZooKeeper.")
                rich.print("[yellow]💡 Close VSCode when you're done viewing.")
            else:
                rich.print(f"[blue]👀 Watching for changes in {temp_dir}...")
                rich.print(
                    f"[blue]🔄 Changes will be synced to ZooKeeper every {sync_interval} seconds"
                )
                rich.print(
                    "[yellow]💡 Make your changes in VSCode. Changes will be synced automatically. Close VSCode when you're done."
                )
                event_handler = ZooKeeperSyncHandler(
                    get_client(), temp_dir, znode_path, sync_interval, reload=reload
                )
                observer = Observer()
                observer.schedule(event_handler, temp_dir, recursive=True)
                observer.start()

            try:
                while True:
                    time.sleep(1)

                    if vscode_process and vscode_process.poll() is not None:
                        rich.print("[warning]🚪 VSCode has been closed. Exiting...")
                        if event_handler and event_handler.pending_changes:
                            rich.print("[blue]🔄 Final sync before exit...")
                            event_handler._sync_changes()
                        break

            except KeyboardInterrupt:
                rich.print("\n[warning]🛑 Stopping session...")
            finally:
                if vscode_process and vscode_process.poll() is None:
                    rich.print("[blue]🔄 Closing VSCode...")
                    vscode_process.terminate()
                    try:
                        vscode_process.wait(timeout=5)
                    except subprocess.TimeoutExpired:
                        rich.print("[warning]⚠️ Force killing VSCode...")
                        vscode_process.kill()

                if observer:
                    observer.stop()
                    observer.join()

        except Exception as e:
            rich.print(f"[error]❌ Unexpected error: {e}")
            raise typer.Exit(1)
        finally:
            rich.print(
                "[success]🧹 Temporary directory will be automatically cleaned up"
            )


@app.command()
def edit(
        znode_path: str = typer.Argument("/configs", help="Path of the zNode to edit", autocompletion=znode_paths),
        sync_interval: int = typer.Option(
            5, "--sync-interval", "-s", help="Sync interval in seconds"
        ),
        reload: bool = typer.Option(False, "--reload",
                                    help="Automatically reloads collections whose configs have been edited, in real-time up to sync-interval")
):
    """Interactively view and edit ZooKeeper."""
    _open_znode_session(
        znode_path=znode_path,
        sync_interval=sync_interval,
        reload=reload,
        read_only=False,
    )


@app.command()
def view(
        znode_path: str = typer.Argument("/configs", help="Path of the zNode to view", autocompletion=znode_paths),
):
    """Open a read-only view of a ZooKeeper zNode in VSCode."""

    _open_znode_session(
        znode_path=znode_path,
        sync_interval=5,
        reload=False,
        read_only=True,
    )


def _render_config_directory(config_dir: Path, templates_dir: Path, rendered_dir: Path) -> list[Path]:
    env = Environment(
        loader=ChoiceLoader([
            FileSystemLoader(str(config_dir)),
            FileSystemLoader(str(templates_dir)),
        ])
    )
    rendered_files: list[Path] = []

    for source_path in sorted(config_dir.rglob("*")):
        relative_path = source_path.relative_to(config_dir)
        destination_path = rendered_dir / relative_path

        if source_path.is_dir():
            destination_path.mkdir(parents=True, exist_ok=True)
            continue

        destination_path.parent.mkdir(parents=True, exist_ok=True)
        template = env.get_template(str(relative_path).replace("\\", "/"))
        destination_path.write_text(template.render(), encoding="utf-8")
        rendered_files.append(destination_path)

    return rendered_files


def _render_jinja_tree(root_dir: Path, rendered_dir: Path | None = None) -> tuple[Path, list[Path]]:
    root_dir = root_dir.expanduser().resolve()
    if rendered_dir is None:
        rendered_dir = root_dir / "rendered"
    else:
        rendered_dir = rendered_dir.expanduser().resolve()

    jinja_dir = root_dir / "jinja"
    templates_dir = jinja_dir / "templates"
    configs_dir = jinja_dir / "configs"
    resources_dir = jinja_dir / "resources"

    if not jinja_dir.is_dir():
        raise AdmException(f"Expected a jinja directory under {root_dir}")
    if not templates_dir.is_dir():
        raise AdmException(f"Expected a templates directory under {templates_dir}")
    if not configs_dir.is_dir():
        raise AdmException(f"Expected a configs directory under {configs_dir}")

    if rendered_dir.exists():
        shutil.rmtree(rendered_dir)
    rendered_dir.mkdir(parents=True, exist_ok=True)

    rendered_file_paths: set[Path] = set()
    config_subdirs = sorted(path for path in configs_dir.iterdir() if path.is_dir())
    if not config_subdirs:
        raise AdmException(f"No configuration subdirectories were found under {configs_dir}")

    for config_subdir in config_subdirs:
        config_rendered_dir = rendered_dir / config_subdir.name
        if resources_dir.is_dir():
            shutil.copytree(resources_dir, config_rendered_dir, dirs_exist_ok=True)
            rendered_file_paths.update(
                path for path in config_rendered_dir.rglob("*") if path.is_file()
            )

        rendered_file_paths.update(
            _render_config_directory(
                config_dir=config_subdir,
                templates_dir=templates_dir,
                rendered_dir=config_rendered_dir,
            )
        )

    return rendered_dir, sorted(rendered_file_paths)


def _prepare_upload_paths(
        paths: list[Path],
        *,
        znode_path: str,
        no_render: bool = False,
) -> list[Path]:
    prepared_paths: list[Path] = []

    for path in paths:
        if no_render:
            prepared_paths.append(path)
            continue

        if not (path / "jinja").is_dir():
            rich.print(
                    f"[warning]⚠️  /jinja directory at {path} was not found! Skipping templating for path..."
                )
            prepared_paths.append(path)
            continue

        rendered_dir, rendered_files = _render_jinja_tree(path)
        rich.print(f"[success]✅ Rendered {len(rendered_files)} files into [bold]{rendered_dir}[/]")

        if znode_path == "/configs":
            prepared_paths.extend(sorted(subdir for subdir in rendered_dir.iterdir() if subdir.is_dir()))
        else:
            prepared_paths.append(rendered_dir)

    return prepared_paths


@app.command(help="Render Jinja templates using config subdirectories and write results to a sibling rendered directory.")
def render(
        dir: Path | None = typer.Argument(
            None,
            exists=False,
            file_okay=False,
            resolve_path=False,
            help="Directory containing a jinja/templates and jinja/configs tree; defaults to the configured configsets directory",
        ),
):
    if not isinstance(dir, Path):
        dir = None

    if dir is not None:
        dir = dir.expanduser().resolve()
    else:
        try:
            dir = get_default_configsets_config_dir()
        except TypeError:
            dir = None

    if dir is None:
        rich.print(
            "[error]❌ Default configsets directory is not configured. "
            "Use sa context config-dir to set it or provide a directory."
        )
        raise typer.Exit(1)

    if not dir.is_dir():
        rich.print(f"[error]❌ Provided directory {dir} does not exist or is not a directory")
        raise typer.Exit(1)

    rendered_dir, rendered_files = _render_jinja_tree(dir)
    rich.print(f"[success]✅ Rendered {len(rendered_files)} files into [bold]{rendered_dir}[/]")



def _load_local_config_files(config_dir: Path) -> dict[str, str]:
    files: dict[str, str] = {}
    for abs_path, rel_path in iter_local_files(config_dir, None, None):
        files[rel_path] = abs_path.read_text(encoding="utf-8", errors="ignore")
    return files


def _print_diff_lines(diff_lines: list[str]) -> None:
    for line in diff_lines:
        style = None
        if line.startswith("@@"):
            style = "cyan"
        elif line.startswith("+++ ") or line.startswith("--- "):
            style = "bright_black"
        elif line.startswith("+"):
            style = "green"
        elif line.startswith("-"):
            style = "red"

        rich.print(Text(line, style=style))


def _show_config_diff(local_config_dirs: dict[str, Path]) -> None:
    zk_client = get_client()

    for idx, config_name in enumerate(sorted(local_config_dirs)):
        if idx:
            rich.print()

        rich.print(
            Panel.fit(
                f"Comparing configset [bold]{config_name}[/]",
                title="ZooKeeper Diff",
            )
        )

        local_dir = local_config_dirs[config_name]
        local_files = _load_local_config_files(local_dir) if local_dir.is_dir() else {}
        if not local_dir.is_dir():
            rich.print(
                f"[warning]⚠️ Configset {config_name} was not found locally! Searched under {local_dir}..."
            )
            continue

        zk_path = f"/configs/{config_name}"
        zk_files = collect_znode_files(zk_client, zk_path)
        if not zk_files and not zk_client.exists(zk_path):
            rich.print(
                f"[warning]⚠️ Configset {config_name} was not found in ZooKeeper! Searched under {zk_path}..."
            )
            continue

        file_paths = sorted(set(local_files) | set(zk_files))
        if not file_paths:
            rich.print("[yellow]No files to compare.")
            continue

        for rel_path in file_paths:
            local_content = local_files.get(rel_path)
            if not local_content:
                rich.print(
                    f"[warning]⚠️ File {rel_path} was not found locally! Searched under {local_dir / rel_path}..."
                )
                continue
            zk_content = zk_files.get(rel_path, "")
            if not zk_content:
                rich.print(
                    f"[warning]⚠️ File {rel_path} was not found in ZooKeeper! Searched under {zk_path}/{rel_path}..."
                )
                continue

            diff_lines = list(
                difflib.unified_diff(
                    zk_content.splitlines(),
                    local_content.splitlines(),
                    fromfile=f"ZooKeeper/{rel_path}",
                    tofile=f"Local/{rel_path}",
                    lineterm="",
                    n=1,
                )
            )

            if not diff_lines:
                continue

            rich.print(f"[cyan]{rel_path}[/]")
            _print_diff_lines(diff_lines)
            rich.print()


def _prompt_for_interactive_upload(local_config_dirs: dict[str, Path]) -> None:
    _show_config_diff(local_config_dirs)
    if not Confirm.ask("Approve these ZooKeeper config changes?"):
        rich.print("[warning]⚠️ Upload cancelled")
        raise typer.Exit()


@app.command(help="Show diffs between local configsets and ZooKeeper.")
def diff(
        config_pattern: str = typer.Argument(
            ..., help="Regex used to select configuration names to compare"
        ),
        dir: Path = typer.Option(
            None,
            "--dir",
            "-d",
            file_okay=False,
            help="Override the configsets directory used for local files",
        ),
):
    try:
        pattern = re.compile(config_pattern)
    except re.error as exc:
        raise typer.BadParameter(
            f"Invalid configuration regex '{config_pattern}': {exc}"
        ) from exc

    if dir:
        configsets_dir = dir.expanduser().resolve()
    else:
        try:
            configsets_dir = get_default_configsets_config_dir()
        except TypeError:
            configsets_dir = None
    if configsets_dir is None:
        rich.print(
            "[error]❌ Default configsets directory is not configured. "
            "Use sa context config-dir to set it or provide --dir."
        )
        raise typer.Exit(1)

    if not configsets_dir.is_dir():
        rich.print(
            f"[error]❌ Provided configsets directory {configsets_dir} does not exist or is not a directory"
        )
        raise typer.Exit(1)

    zk_client = get_client()
    try:
        zk_config_names = set(zk_client.get_children("/configs"))
    except Exception:
        zk_config_names = set()

    local_config_names = {p.name for p in configsets_dir.iterdir() if p.is_dir()}
    target_configs = sorted(
        {name for name in local_config_names | zk_config_names if pattern.search(name)}
    )

    if not target_configs:
        rich.print("[warning]⚠️ No configurations matched the provided pattern")
        raise typer.Exit(1)

    _show_config_diff({name: configsets_dir / name for name in target_configs})


@app.command(help="Upload local files or directories to a ZooKeeper znode.")
def upload(
        paths: List[str] = typer.Argument(
            ...,
            exists=False,
            resolve_path=False,
            help="Local paths to copy to ZooKeeper. This may also just be a config name (it will be uploaded from the default configuration directory)",
            autocompletion=config_names_or_paths,
        ),
        znode_path: str = typer.Option("/configs", help="zNode path to copy to", autocompletion=znode_paths),
        include: List[str] | None = typer.Option(
            None,
            "--include",
            help="Regex to include files/directories within the specified paths (matched against relative paths); exclude patterns take precedence. For example, only update the schema.xml by specifying it.",
        ),
        exclude: List[str] | None = typer.Option(
            None,
            "--exclude",
            help="Regex to exclude files/directories within the specified paths (matched against relative paths and applied before includes). For example, exclude resource files like usersBlacklist.txt.",
        ),
        only_used: bool = typer.Option(
            True,
            "--only-used/--all",
            help="Upload only configs referenced by collections",
        ),
        reload: bool = typer.Option(
            False,
            "--reload",
            help="Reload collections whose configs were uploaded",
        ),
        reload_exclude: List[str] | None = typer.Option(
            None,
            "--reload-exclude",
            "--exclude-collection",
            help="Collections to exclude from reloading",
            autocompletion=collection_names,
        ),
        skip_checks: bool = typer.Option(False, "--skip-confirm", "-y", help="Skip confirmation prompt"),
        interactive: bool = typer.Option(
            False,
            "--interactive",
            "-i",
            help="Show zoo diff output for config uploads and require approval before uploading",
        ),
        no_render: bool = typer.Option(
            False,
            "--no-render",
            "-r",
            help="Skip rendering Jinja workspaces before uploading",
        ),
):
    """Upload local files or directories to a ZooKeeper znode.

    Exclude patterns are evaluated before include patterns when filtering discovered files.
    """

    skip_checks = _coerce_optional_option(skip_checks)
    interactive = _coerce_optional_option(interactive)

    if only_used and znode_path != "/configs":
        rich.print("[error] ❌ You cannot use only_used when the znode_path is not /configs!")
        raise typer.Exit(1)

    if interactive and znode_path != "/configs":
        rich.print("[error] ❌ --interactive is only supported when uploading to /configs")
        raise typer.Exit(1)

    include_regexes = compile_regex_patterns(include, "--include")
    exclude_regexes = compile_regex_patterns(exclude, "--exclude")
    resolved_paths = []
    for path in paths:
        resolved_paths.append(resolve_config_name_to_abs_or_default_directory(path))
    if not no_render:
        resolved_paths = _prepare_upload_paths(
            resolved_paths,
            znode_path=znode_path,
            no_render=no_render,
        )

    if znode_path == "/configs":
        files_by_config = build_files_by_config(
            [(p, None) for p in resolved_paths],
            znode_path,
            include_regexes=include_regexes,
            exclude_regexes=exclude_regexes,
        )
        files_to_upload = [file for files in files_by_config.values() for file in files]

        if not files_by_config:
            rich.print("[warning]⚠️ No files to upload")
            raise typer.Exit(1)

        cluster_state = get_collections()

        if only_used or not skip_checks or reload:
            config_usage = {
                cfg: get_collections_using_config(cluster_state, cfg)
                for cfg in files_by_config
            }

            if only_used:
                files_by_config = {
                    cfg: files
                    for cfg, files in files_by_config.items()
                    if config_usage[cfg]
                }
                config_usage = {cfg: config_usage[cfg] for cfg in files_by_config}
                if not files_by_config:
                    rich.print("[warning]⚠️ No configurations used by any collection")
                    raise typer.Exit()

            if not skip_checks:
                table = Table(title="Configurations to upload")
                table.add_column("Config")
                table.add_column("Collections using config")
                for cfg, cols in config_usage.items():
                    table.add_row(cfg, ", ".join(c.name for c in cols) if cols else "-")
                rich.print(table)

        if interactive:
            _prompt_for_interactive_upload(
                {path.name: path for path in resolved_paths if path.name in files_by_config}
            )
    else:
        files_to_upload = []
        for path in resolved_paths:
            for full_path, rel_path in iter_local_files(path, include_regexes, exclude_regexes):
                files_to_upload.append((full_path, win_path_to_zk_path(rel_path, znode_path)))


        if not skip_checks:
            table = Table(title="Files to upload")
            table.add_column("Local File")
            table.add_column("zNode Path")

            for file, zk_path in files_to_upload:
                table.add_row(file, zk_path)

            rich.print(table)

    if not interactive and not skip_checks and not Confirm.ask("Proceed with upload?"):
        raise typer.Exit()

    for local_path, zk_path in files_to_upload:
        with open(local_path, "rb") as f:
            create_or_update(get_client(), zk_path, f.read())

    if reload and znode_path == "/configs":
        to_reload = set()
        for cfg, cols in config_usage.items():
            for col in cols:
                if reload_exclude and col.name in reload_exclude:
                    continue
                to_reload.add(col.name)
        if len(to_reload) > 0:
            from solradm.commands.collections.maintenance import reload as reload_cmd
            asyncio.run(
                reload_cmd(
                    collection_name_filter=
                    r"^(" + "|".join(re.escape(c) for c in to_reload) + r")$",
                    dry_run=False, coordinators=None
            )
            )
            asyncio.run(get_initialized_session().close())


@app.command()
@with_cluster_state(CollectionNameFilter)
def sync(
        cluster_state: List[Collection],
        dir: Path = typer.Option(
            None,
            "--dir",
            "-d",
            file_okay=False,
            help="Override the default configsets directory when locating configs to upload",
        ),
        reload: bool = typer.Option(
            False,
            "--reload",
            help="Reload the selected collections after syncing their configs",
        ),
        interactive: bool = typer.Option(
            False,
            "--interactive",
            "-i",
            help="Show zoo diff output and require approval before syncing configs",
        ),
        no_render: bool = typer.Option(
            False,
            "--no-render",
            "-r",
            help="Skip rendering Jinja workspaces before syncing configs",
        ),
):
    """Upload configsets used by selected collections and optionally reload them."""

    interactive = _coerce_optional_option(interactive)

    config_names = sorted({collection.configName for collection in cluster_state})

    if dir is not None:
        dir = dir.expanduser().resolve()
        if not dir.is_dir():
            rich.print(f"[error]❌ Provided directory {dir} does not exist or is not a directory")
            raise typer.Exit(1)

        missing_configs = [name for name in config_names if not (dir / name).exists()]
        if missing_configs:
            rich.print(
                "[error]❌ Could not find the following configsets in the provided directory: "
                + ", ".join(sorted(missing_configs))
            )
            raise typer.Exit(1)

        upload_targets = [str((dir / name).resolve()) for name in config_names]
    else:
        upload_targets = config_names

    upload(
        paths=upload_targets,
        znode_path="/configs",
        include=None,
        exclude=None,
        only_used=True,
        reload=False,
        reload_exclude=None,
        skip_checks=False,
        interactive=interactive,
        no_render=no_render,
    )

    if reload:
        from solradm.commands.collections.maintenance import reload as reload_cmd

        asyncio.run(
            reload_cmd(
                cluster_state=cluster_state,
                coordinators=None,
                skip_checks=False,
                dry_run=False,
            )
        )
