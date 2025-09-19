import os
import shutil
import subprocess
from pathlib import Path
from typing import Dict, List, Tuple

import rich
from kazoo.client import KazooClient

from solradm.exceptions.adm_exception import AdmException


def open_vscode(directory: str):
    """Open VSCode on the specified directory and return the process."""
    try:
        code_abs_location = shutil.which("code")

        if not code_abs_location:
            raise FileNotFoundError

        # Start VSCode as a subprocess and return it
        process = subprocess.Popen(
            [code_abs_location, "--new-window", "--wait", directory],
            env=os.environ.copy(),
        )
        rich.print(
            f"[success]🚀 Opened VSCode on {directory} (PID: {process.pid})"
        )
        return process
    except (subprocess.CalledProcessError, FileNotFoundError):
        raise AdmException("Failed to open VSCode")


def create_or_update(zk: KazooClient, path: str, data: bytes) -> None:
    if not zk.exists(path):
        zk.create(path, data, makepath=True)
        rich.print(f"[success]➕  Created: {path}")
    else:
        zk.set(path, data)
        rich.print(f"[blue]📝 Updated: {path}")


def get_relative_znode_path(
        base_znode_path: str, base_dir_path: str, target_file_path: str
) -> str:
    """Map a local file path to the target ZooKeeper path."""

    rel_path = os.path.relpath(target_file_path, base_dir_path).replace("\\", "/")
    base_normalized = base_znode_path.rstrip("/") or "/"
    base_name = base_normalized.split("/")[-1] if base_normalized not in {"", "/"} else ""

    trimmed_rel_path = rel_path
    if base_name:
        if rel_path == base_name:
            trimmed_rel_path = ""
        elif rel_path.startswith(f"{base_name}/"):
            trimmed_rel_path = rel_path[len(base_name) + 1:]

    if trimmed_rel_path:
        zk_path = "/".join(filter(None, [base_normalized, trimmed_rel_path])).replace("//", "/")
    else:
        zk_path = base_normalized or "/"

    if not zk_path.startswith("/"):
        zk_path = f"/{zk_path}"

    return zk_path


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
