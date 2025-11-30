import os
import re
import shutil
import subprocess
from pathlib import Path
from typing import Dict, Iterator, List, Sequence, Tuple

import rich
import typer
from typer.models import OptionInfo
from kazoo.client import KazooClient
from kazoo.exceptions import NoNodeError

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


def compile_regex_patterns(patterns: Sequence[str] | None, option_display: str) -> list[re.Pattern[str]]:
    if isinstance(patterns, OptionInfo):
        patterns = None
    compiled: list[re.Pattern[str]] = []
    for pattern in patterns or []:
        try:
            compiled.append(re.compile(pattern))
        except re.error as exc:
            raise typer.BadParameter(
                f"Invalid regular expression for {option_display} '{pattern}': {exc}"
            ) from exc
    return compiled


def _normalize_rel_path(path: str) -> str:
    return path.replace("\\", "/")


def _is_excluded(
        rel_path: str, exclude_patterns: Sequence[re.Pattern[str]] | None
) -> bool:
    normalized = _normalize_rel_path(rel_path)
    return bool(exclude_patterns and any(pattern.search(normalized) for pattern in exclude_patterns))


def should_include_path(
        rel_path: str,
        include_patterns: Sequence[re.Pattern[str]] | None,
        exclude_patterns: Sequence[re.Pattern[str]] | None,
) -> bool:
    normalized = _normalize_rel_path(rel_path)

    if exclude_patterns and any(pattern.search(normalized) for pattern in exclude_patterns):
        return False
    if include_patterns and not any(pattern.search(normalized) for pattern in include_patterns):
        return False
    return True


def iter_local_files(
        path: Path,
        include_patterns: Sequence[re.Pattern[str]] | None,
        exclude_patterns: Sequence[re.Pattern[str]] | None,
) -> Iterator[Tuple[Path, str]]:
    """Yield (absolute_path, relative_path) pairs filtered by include/exclude patterns.

    Exclude patterns are applied before includes. Directory traversal is pruned when a
    directory matches an exclude pattern.
    """

    if path.is_file():
        if should_include_path(path.name, include_patterns, exclude_patterns):
            yield path, path.name
        return

    for dirpath, dirnames, filenames in os.walk(path):
        rel_dir = os.path.relpath(dirpath, path)
        rel_dir = "" if rel_dir == "." else rel_dir

        filtered_dirnames = []
        for dirname in dirnames:
            dir_rel = f"{rel_dir}/{dirname}" if rel_dir else dirname
            if _is_excluded(dir_rel, exclude_patterns):
                continue
            filtered_dirnames.append(dirname)
        dirnames[:] = filtered_dirnames

        for filename in filenames:
            rel_file = f"{rel_dir}/{filename}" if rel_dir else filename
            if should_include_path(rel_file, include_patterns, exclude_patterns):
                yield Path(dirpath) / filename, _normalize_rel_path(rel_file)


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


def build_files_by_config(
        paths: List[Tuple[Path, str | None]],
        znode_path: str,
        *,
        include_regexes: Sequence[re.Pattern[str]] | None = None,
        exclude_regexes: Sequence[re.Pattern[str]] | None = None,
) -> Dict[str, List[Tuple[Path, str]]]:
    files_by_config: Dict[str, List[Tuple[Path, str]]] = {}
    for path, override in paths:
        config = override or path.name
        for sub_file, rel_path in iter_local_files(path, include_regexes, exclude_regexes):
            zk_path = f"{znode_path.rstrip('/')}/{config}/{rel_path}"
            files_by_config.setdefault(config, []).append((sub_file, zk_path))
    return files_by_config


def collect_znode_files(zk: KazooClient, base_path: str) -> Dict[str, str]:
    """Collect file-like zNode contents beneath ``base_path``.

    Leaf nodes are treated as files; their data is decoded as UTF-8 with
    replacement to avoid errors. Intermediate nodes with children are traversed.
    Missing nodes return an empty mapping.
    """

    collected: Dict[str, str] = {}
    normalized_base = base_path.rstrip("/") or "/"

    def _walk(path: str, rel: str = "") -> None:
        try:
            children = zk.get_children(path)
        except NoNodeError:
            return

        if not children:
            data, _ = zk.get(path)
            rel_path = rel or Path(path).name
            collected[rel_path] = (data or b"").decode("utf-8", errors="ignore")
            return

        for child in children:
            child_path = f"{path.rstrip('/')}/{child}" if path != "/" else f"/{child}"
            child_rel = f"{rel}/{child}" if rel else child
            _walk(child_path, child_rel)

    _walk(normalized_base, "")
    return collected
