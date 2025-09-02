import os
import shutil
import subprocess

from kazoo.client import KazooClient
from pathlib import Path
from typing import Dict, List, Tuple
from solradm.lazy import lazy_module


rich = lazy_module("rich")


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


def build_files_by_config(paths: List[Tuple[Path, str | None]], znode_path: str) -> Dict[str, List[Tuple[Path, str]]]:
    files_by_config: Dict[str, List[Tuple[Path, str]]] = {}
    for path, override in paths:
        config = override or path.name
        if path.is_file():
            rel_path = path.name
            zk_path = f"{znode_path.rstrip('/')}/{config}/{rel_path}"
            files_by_config.setdefault(config, []).append((path, zk_path))
        elif path.is_dir():
            for sub_file in path.rglob("*"):
                if sub_file.is_file():
                    rel_path = os.path.relpath(sub_file, path).replace("\\", "/")
                    zk_path = f"{znode_path.rstrip('/')}/{config}/{rel_path}"
                    files_by_config.setdefault(config, []).append((sub_file, zk_path))
    return files_by_config
