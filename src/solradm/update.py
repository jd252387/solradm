import importlib.metadata
import json
import os
import re
import subprocess
import time
from pathlib import Path
from typing import Optional, Tuple

from rich.console import Console

CACHE_PATH = Path.home() / ".cache" / "solradm" / "update.json"


def _parse_version(ver: str) -> Tuple[int, ...]:
    return tuple(int(part) for part in re.split(r"[^0-9]+", ver) if part)


def _load_cache() -> Tuple[Optional[str], float]:
    try:
        with CACHE_PATH.open("r") as fh:
            data = json.load(fh)
            return data.get("latest"), data.get("checked_at", 0.0)
    except Exception:
        return None, 0.0


def _save_cache(version: str) -> None:
    try:
        CACHE_PATH.parent.mkdir(parents=True, exist_ok=True)
        with CACHE_PATH.open("w") as fh:
            json.dump({"latest": version, "checked_at": time.time()}, fh)
    except Exception:
        pass


def _fetch_latest() -> Optional[str]:
    try:
        result = subprocess.run(
            ["pip", "index", "versions", "solradm"],
            capture_output=True,
            text=True,
            timeout=5,
            env={**os.environ, "PIP_DISABLE_PIP_VERSION_CHECK": "1"},
        )
    except Exception:
        return None
    if result.returncode != 0:
        return None
    match = re.search(r"Available versions:\s*(.+)", result.stdout)
    if not match:
        return None
    versions = [v.strip() for v in match.group(1).split(",") if v.strip()]
    return versions[0] if versions else None


def notify_if_outdated() -> None:
    try:
        current_str = importlib.metadata.version("solradm")
    except importlib.metadata.PackageNotFoundError:
        return
    current = _parse_version(current_str)

    latest_str, checked_at = _load_cache()
    if not latest_str or time.time() - checked_at > 3600:
        latest_str = _fetch_latest()
        if not latest_str:
            _save_cache(current_str)
            return
        _save_cache(latest_str)

    latest = _parse_version(latest_str)
    if latest > current:
        console = Console()
        console.print(
            f"[yellow]A new version of solradm ({latest_str}) is available.[/yellow] "
            f"You are using {current_str}. "
            f"Upgrade using: [bold]pip install --upgrade solradm[/bold]",
        )
