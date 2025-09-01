import subprocess
import tempfile
import time
from pathlib import Path
from typing import List

import rich
import typer
from rich.panel import Panel
from rich.text import Text
from watchdog.observers import Observer

from solradm.commands.zk.utils import (
    open_vscode,
    upload_to_zk,
)
from solradm.commands.zk.utils.sync_handler import ZooKeeperSyncHandler
from solradm.commands.zk.utils.znode_copier import copy_znode_to_local
from solradm.zk import get_client
from solradm.config.util import get_configsets_dir

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
    reload: bool = typer.Option(False, "--reload", help="Automatically reloads collections whose configs have been edited, in real-time up to sync-interval")
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
    paths: List[Path] | None = typer.Argument(
        None,
        exists=True,
        resolve_path=True,
        help="Paths to copy to ZooKeeper (defaults to the configsets directory)",
    ),
    znode_path: str = typer.Option("/configs", help="Path of the zNode to copy"),
):
    """Upload local files or directories to a ZooKeeper znode."""

    if not paths:
        try:
            paths = [get_configsets_dir()]
        except Exception:
            raise typer.BadParameter(
                "Please configure a valid configuration directory using 'solradm config edit-configdir'"
            )

    upload_to_zk(paths, znode_path)

