import asyncio
import os
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
from watchdog.observers import Observer

from solradm.api import get_initialized_sesssion
from solradm.api.state import get_collections
from solradm.api.utils import get_collections_using_config
from solradm.commands.zk.utils import (
    open_vscode,
    create_or_update,
    build_files_by_config,
)
from solradm.commands.zk.utils.sync_handler import ZooKeeperSyncHandler
from solradm.commands.zk.utils.znode_copier import copy_znode_to_local
from solradm.completion.collections import collection_names
from solradm.config.util import resolve_config_name_to_abs_or_default_directory
from solradm.zk import get_client

app = typer.Typer()


@app.command()
def edit(
        znode_path: str = typer.Argument("/configs", help="Path of the zNode to edit"),
        sync_interval: int = typer.Option(
            5, "--sync-interval", "-s", help="Sync interval in seconds"
        ),
        no_data: bool = typer.Option(False, "--no-data", help="Skip copying zNode data"),
        no_vscode: bool = typer.Option(
            False, "--no-vscode", help="Don't open VSCode automatically"
        ),
        reload: bool = typer.Option(False, "--reload",
                                    help="Automatically reloads collections whose configs have been edited, in real-time up to sync-interval")
):
    """Interactively view and edit ZooKeeper."""

    rich.print(
        Panel.fit(
            Text("ZNode Copier & Sync Tool"),
            title="🚀 ZooKeeper Integration",
        )
    )

    # Create temporary directory
    with tempfile.TemporaryDirectory() as temp_dir:
        rich.print(f"[blue]📁 Created temporary directory: {temp_dir}")

        try:
            # Copy zNode to temporary directory
            rich.print(f"[blue]📋 Copying zNode {znode_path} to temporary directory...")
            if not copy_znode_to_local(
                    zk=get_client(),
                    znode_path=znode_path,
                    local_dir=temp_dir,
                    include_data=not no_data,
            ):
                raise typer.Exit(1)

            # Open VSCode if requested
            vscode_process = None
            if not no_vscode:
                vscode_process = open_vscode(temp_dir)
                if not vscode_process:
                    rich.print("[warning]⚠️ Continuing without VSCode...")

            # Set up file watching and syncing
            rich.print(f"[blue]👀 Watching for changes in {temp_dir}...")
            rich.print(
                f"[blue]🔄 Changes will be synced to ZooKeeper every {sync_interval} seconds"
            )
            if not no_vscode:
                rich.print(
                    "[yellow]💡 Make your changes in VSCode. Changes will be synced automatically. Close VSCode when you're done."
                )
            else:
                rich.print("[yellow]💡 Press Ctrl+C to stop watching.")

            # Create watchdog observer
            event_handler = ZooKeeperSyncHandler(
                get_client(), temp_dir, znode_path, sync_interval, reload=True
            )
            observer = Observer()
            observer.schedule(event_handler, temp_dir, recursive=True)
            observer.start()

            try:
                # Keep the script running and monitor VSCode process
                while True:
                    time.sleep(1)

                    # Check if VSCode process has exited
                    if vscode_process and vscode_process.poll() is not None:
                        rich.print("[warning]🚪 VSCode has been closed. Exiting...")
                        # Final sync before exiting
                        if event_handler.pending_changes:
                            rich.print("[blue]🔄 Final sync before exit...")
                            event_handler._sync_changes()
                        break

            except KeyboardInterrupt:
                rich.print("\n[warning]🛑 Stopping file watcher...")
            finally:
                # Clean up
                if vscode_process and vscode_process.poll() is None:
                    rich.print("[blue]🔄 Closing VSCode...")
                    vscode_process.terminate()
                    try:
                        vscode_process.wait(timeout=5)
                    except subprocess.TimeoutExpired:
                        rich.print("[warning]⚠️ Force killing VSCode...")
                        vscode_process.kill()

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
def upload(
        paths: List[Path] = typer.Argument(
            ...,
            exists=False,
            resolve_path=False,
            help="Local paths to copy to ZooKeeper. This may also just be a config name (it will be uploaded from the default configuration directory)",
        ),
        znode_path: str = typer.Option("/configs", help="zNode path to copy to"),
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
        exclude: List[str] | None = typer.Option(
            None,
            "--exclude",
            help="Collections to exclude from reloading",
            autocompletion=collection_names,
        ),
        skip_checks: bool = typer.Option(False, "--skip-confirm", "-y", help="Skip confirmation prompt"),
):
    if only_used and znode_path != "/configs":
        rich.print("[error] ❌ You cannot use only_used when the znode_path is not /configs!")
        raise typer.Exit(1)

    """Upload local files or directories to a ZooKeeper znode."""
    resolved_paths = []
    for path in paths:
        resolved_paths.append(resolve_config_name_to_abs_or_default_directory(path))

    if znode_path == "/configs":
        files_by_config = build_files_by_config([(p, None) for p in resolved_paths], znode_path)
        files_to_upload = []

        for cfg, files_to_upload in files_by_config.items():
            files_to_upload.extend(files_to_upload)

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
            for root, _, files_to_upload in os.walk(path):
                for file in files_to_upload:
                    files_to_upload.append((os.path.join(root, file), znode_path))

        if not skip_checks:
            table = Table(title="Files to upload")
            table.add_column("Local File")
            table.add_column("zNode Path")

            for file in files_to_upload:
                table.add_row(file, znode_path)

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
                if exclude and col.name in exclude:
                    continue
                to_reload.add(col.name)
        if to_reload:
            from solradm.commands.collections import reload as reload_cmd
            asyncio.run(
                reload_cmd(
                    collection_name_filter=
                    r"^(" + "|".join(re.escape(c) for c in to_reload) + r")$",
                    dry_run=False, coordinators=None
                )
            )
            asyncio.run(get_initialized_sesssion().close())
