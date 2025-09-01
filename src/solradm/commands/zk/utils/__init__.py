import os
import shutil
import subprocess
from pathlib import Path
from typing import Iterable, List, Tuple

import rich
from kazoo.client import KazooClient
from solradm.zk import get_client


def open_vscode(directory: str):
    """Open VSCode on the specified directory and return the process."""
    try:
        # Start VSCode as a subprocess and return it
        process = subprocess.Popen(
            [shutil.which("code"), "--new-window", "--wait", directory],
            env=os.environ.copy(),
        )
        rich.print(
            f"[success]🚀 Opened VSCode on {directory} (PID: {process.pid})"
        )
        return process
    except subprocess.CalledProcessError:
        rich.print(
            "[error]❌ Failed to open VSCode. Make sure 'code' command is available in PATH"
        )
        return None
    except FileNotFoundError:
        rich.print(
            "[error]❌ VSCode command 'code' not found. Please install VSCode and add it to PATH"
        )
        return None

def create_or_update(zk: KazooClient, path: str, data: bytes) -> None:
    if not zk.exists(path):
        zk.create(path, data, makepath=True)
        rich.print(f"[success]➕  Created: {path}")
    else:
        zk.set(path, data)
        rich.print(f"[blue]📝 Updated: {path}")

def get_relative_znode_path(base_znode_path: str, base_dir_path: str, target_file_path: str
) -> str:
    rel_path = os.path.relpath(target_file_path, base_dir_path)

    return rel_path.replace("\\", "/")


def upload_to_zk(paths: Iterable[Path], znode_path: str) -> None:
    file_paths: List[Tuple[Path, Path]] = []
    for path in paths:
        if path.is_file():
            file_paths.append((path, path))
        elif path.is_dir():
            for sub_file in path.rglob("*"):
                if sub_file.is_file():
                    file_paths.append((path, sub_file))

    for base, file_path in file_paths:
        with open(file_path, "rb") as f:
            create_or_update(
                get_client(),
                get_relative_znode_path(znode_path, str(base), str(file_path)),
                f.read(),
            )

