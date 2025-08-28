import hashlib
import os
import shutil
import subprocess
import tempfile
import threading
import time
from pathlib import Path
from typing import List, Tuple

import rich
import typer
from kazoo.client import KazooClient
from kazoo.exceptions import NoNodeError
from rich.panel import Panel
from rich.progress import Progress, SpinnerColumn, TextColumn
from rich.text import Text
from watchdog.events import FileSystemEventHandler
from watchdog.observers import Observer

from solradm.commands.zk.utils import (
    open_vscode,
    create_or_update,
    get_relative_znode_path,
)
from solradm.commands.zk.utils.sync_handler import ZooKeeperSyncHandler
from solradm.commands.zk.utils.znode_copier import copy_znode_to_local
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
                get_client(), temp_dir, znode_path, sync_interval
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
        ..., exists=True, resolve_path=True, help="Paths to copy to ZooKeeper"
    ),
    znode_path: str = typer.Option("/configs", help="Path of the zNode to copy"),
):
    file_paths: List[Tuple[Path, Path]] = []

    for path in paths:
        if path.is_file():
            file_paths.append((path, path))
        elif path.is_dir():
            for sub_file in path.rglob("*"):
                if sub_file.is_file():
                    file_paths.append((path, sub_file))

    for file_path in file_paths:
        with open(file_path[1], "rb") as f:
            create_or_update(get_client(), get_relative_znode_path(znode_path, str(file_path[0]), str(file_path[1])), f.read())