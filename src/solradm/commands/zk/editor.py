import asyncio
import re
import subprocess
import tempfile
import time
from pathlib import Path
from typing import List

import rich
import typer
from rich.panel import Panel
from rich.prompt import Confirm
from rich.table import Table
from rich.text import Text
from rich.diff import Diff
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


@app.command()
def diff(
        config_regex: str = typer.Argument(..., help="Regex of configuration names to diff"),
        dir: Path | None = typer.Option(
            None,
            "--dir",
            "-d",
            file_okay=False,
            help="Override the default configsets directory when locating configs to diff",
        ),
):
    """Diff local configsets against the versions stored in ZooKeeper."""

    try:
        regex = re.compile(config_regex)
    except re.error as exc:
        raise typer.BadParameter(f"Invalid regular expression '{config_regex}': {exc}") from exc

    configsets_dir = _resolve_configsets_dir(dir)
    zk = get_client()

    if not zk.exists("/configs"):
        rich.print("[error]❌ /configs does not exist in ZooKeeper")
        raise typer.Exit(1)

    available_configs = sorted(zk.get_children("/configs"))
    matched_configs = [cfg for cfg in available_configs if regex.search(cfg)]

    if not matched_configs:
        rich.print("[warning]⚠️ No ZooKeeper configsets matched the provided regex")
        raise typer.Exit()

    for config_name in matched_configs:
        rich.print(
            Panel.fit(
                Text(config_name, style="bold"),
                title="Configset",
                subtitle="Diff",
                style="cyan",
            )
        )

        local_path = configsets_dir / config_name
        if not local_path.exists():
            rich.print(f"[warning]⚠️ Local configset '{config_name}' not found at {local_path}")

        local_files = _collect_local_config_files(local_path)
        zk_files = _collect_zk_config_files(f"/configs/{config_name}")

        file_paths = sorted(set(local_files) | set(zk_files))
        if not file_paths:
            rich.print("[yellow]No files found to compare.")
            continue

        for rel_path in file_paths:
            local_content = local_files.get(rel_path)
            zk_content = zk_files.get(rel_path)

            if local_content == zk_content and local_content is not None:
                rich.print(f"[green]✅ {rel_path} is identical")
                continue

            local_title = "Local" if local_content is not None else "Local (missing)"
            zk_title = "ZooKeeper" if zk_content is not None else "ZooKeeper (missing)"

            diff_renderable = Diff.compare(
                local_content or "",
                zk_content or "",
                title1=local_title,
                title2=zk_title,
            )
            rich.print(Panel(diff_renderable, title=rel_path, subtitle=config_name, expand=True))

        rich.print()


def _read_text_file(path: Path) -> str:
    try:
        return path.read_text(encoding="utf-8").replace("\r", "")
    except UnicodeDecodeError:
        return path.read_text(encoding="utf-8", errors="replace").replace("\r", "")


def _collect_local_config_files(config_path: Path) -> dict[str, str]:
    files: dict[str, str] = {}
    if not config_path.exists():
        return files

    for file in sorted(config_path.rglob("*")):
        if file.is_file():
            files[file.relative_to(config_path).as_posix()] = _read_text_file(file)
    return files


def _collect_zk_config_files(base_path: str) -> dict[str, str]:
    files: dict[str, str] = {}
    zk = get_client()

    if not zk.exists(base_path):
        return files

    def _walk(path: str, rel: str = "") -> None:
        children = zk.get_children(path)
        if not children:
            data, _ = zk.get(path)
            files[rel or path.split("/")[-1]] = data.decode("utf-8", errors="replace").replace("\r", "")
            return

        for child in children:
            child_path = f"{path.rstrip('/')}/{child}" if path != "/" else f"/{child}"
            child_rel = f"{rel}/{child}" if rel else child
            _walk(child_path, child_rel)

    _walk(base_path)
    return files


def _resolve_configsets_dir(override_dir: Path | None) -> Path:
    if override_dir is None:
        config_dir = get_default_configsets_config_dir()
        if config_dir is None or not config_dir.is_dir():
            rich.print("[error]❌ Default configsets directory is not configured.")
            raise typer.Exit(1)
        return config_dir

    override_dir = override_dir.expanduser().resolve()
    if not override_dir.is_dir():
        rich.print(f"[error]❌ Provided directory {override_dir} does not exist or is not a directory")
        raise typer.Exit(1)
    return override_dir


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
            help="Regex to include files/directories (matched against relative paths); exclude patterns take precedence",
        ),
        exclude: List[str] | None = typer.Option(
            None,
            "--exclude",
            help="Regex to exclude files/directories (matched against relative paths and applied before includes)",
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
):
    """Upload local files or directories to a ZooKeeper znode.

    Exclude patterns are evaluated before include patterns when filtering discovered files.
    """

    if only_used and znode_path != "/configs":
        rich.print("[error] ❌ You cannot use only_used when the znode_path is not /configs!")
        raise typer.Exit(1)

    include_regexes = compile_regex_patterns(include, "--include")
    exclude_regexes = compile_regex_patterns(exclude, "--exclude")
    resolved_paths = []
    for path in paths:
        resolved_paths.append(resolve_config_name_to_abs_or_default_directory(path))

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

    if not skip_checks and not Confirm.ask("Proceed with upload?"):
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
):
    """Upload configsets used by selected collections and optionally reload them."""

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
    )

    if reload:
        from solradm.commands.collections.maintenance import reload as reload_cmd

        asyncio.run(
            reload_cmd(
                cluster_state=cluster_state,
                coordinators=None,
                skip_checks=False,
            )
        )
