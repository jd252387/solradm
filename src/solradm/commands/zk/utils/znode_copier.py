import os
from pathlib import Path

import rich
from kazoo.client import KazooClient
from kazoo.exceptions import NoNodeError
from rich.progress import Progress, SpinnerColumn, TextColumn


def copy_znode_to_local(
    zk: KazooClient, znode_path: str, local_dir: str, include_data: bool = True
):
    """
    Copy a parent zNode and all its children to a local directory.

    Args:
        zk: Target ZooKeeper
        znode_path: Path of the zNode to copy
        local_dir: Local directory to copy to
        include_data: Whether to include zNode data
    """
    if not zk:
        rich.print("[error]❌ Not connected to ZooKeeper")
        return False

    # Copy the zNode and its children
    with Progress(
        SpinnerColumn(),
        TextColumn("[progress.description]{task.description}"),
    ) as progress:
        task = progress.add_task(f"Copying zNode {znode_path}...", total=None)
        success = _copy_znode_recursive(
            zk, znode_path, local_dir, include_data, progress, task
        )

    if success:
        rich.print(f"[success]✅ Successfully copied zNode {znode_path} to {local_dir}")
    else:
        rich.print(f"[error]❌ Failed to copy zNode {znode_path}")

    return success


def _copy_znode_recursive(
    zk: KazooClient, znode_path: str, local_dir: str, include_data: bool, progress, task
):
    """Recursively copy a zNode and its children."""
    try:
        children = zk.get_children(znode_path)

        if not children:
            data, _ = zk.get(znode_path)

            local_file_path = os.path.join(local_dir, os.path.basename(znode_path))
            with open(local_file_path, "w", encoding="utf-8") as f:
                if data:
                    f.write(data.decode("utf-8"))
            progress.update(task, description=f"Copied data zNode: {znode_path}")
        else:
            for child in children:
                child_path = (
                    f"{znode_path}/{child}" if znode_path != "/" else f"/{child}"
                )
                child_local_dir = os.path.join(local_dir, os.path.basename(znode_path))

                # Create child directory if it doesn't exist
                Path(child_local_dir).mkdir(parents=True, exist_ok=True)

                # Recursively copy child
                _copy_znode_recursive(
                    zk, child_path, child_local_dir, include_data, progress, task
                )

        return True

    except NoNodeError:
        rich.print(f"[warning]⚠️ zNode {znode_path} does not exist")
        return False
    except Exception as e:
        rich.print(f"[error]❌ Error copying zNode {znode_path}: {e}")
        return False
