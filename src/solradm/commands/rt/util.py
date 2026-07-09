"""Shared helpers for the ``rt`` (Artifactory) subcommands.

Ported from the standalone ``airgap/offline_deps.py`` script. The ad-hoc ANSI
``log/err/warn/die`` helpers are mapped onto solradm's ``rich.print`` styles, and
``die`` raises ``typer.Exit`` instead of calling ``sys.exit`` so it propagates
cleanly through Typer.
"""

import shutil
import subprocess
import urllib.request
import zipfile
from contextlib import contextmanager
from pathlib import Path

import rich
import typer

from solradm.commands.rt.config import RtConfig


# ── Logging ──────────────────────────────────────────────────────────────────
def log(msg: str) -> None:
    rich.print(f"[blue]==>[/] {msg}")


def err(msg: str) -> None:
    rich.print(f"[error]❌ {msg}")


def warn(msg: str) -> None:
    rich.print(f"[warning]⚠️  {msg}")


def die(msg: str) -> None:
    err(msg)
    raise typer.Exit(1)


def run(cmd: list, cwd: Path | None = None) -> None:
    """Run an external command, exiting cleanly (no traceback) on failure."""
    proc = subprocess.run([str(c) for c in cmd], cwd=cwd)
    if proc.returncode != 0:
        die(f"Command failed ({proc.returncode}): {' '.join(str(c) for c in cmd)}")


def human_size(num_bytes: int) -> str:
    """Approximate `du -h` output (1024-based, single-letter suffixes)."""
    size = float(num_bytes)
    for unit in ("B", "K", "M", "G"):
        if size < 1024:
            return f"{int(size)}{unit}" if unit == "B" else f"{size:.1f}{unit}"
        size /= 1024
    return f"{size:.1f}T"


# ── Downloading ────────────────────────────────────────────────────────────────
def download(url: str, dest: Path, retries: int = 3) -> None:
    """Download url -> dest, following redirects and retrying on failure (curl -fL --retry 3)."""
    last_error: Exception | None = None
    for attempt in range(1, retries + 1):
        try:
            with urllib.request.urlopen(url) as resp, open(dest, "wb") as out:
                shutil.copyfileobj(resp, out)
            return
        except Exception as exc:  # noqa: BLE001 — retry on any transport/HTTP error
            last_error = exc
            if attempt < retries:
                log(f"Download failed (attempt {attempt}/{retries}): {exc}; retrying…")
    die(f"Failed to download {url}: {last_error}")


def download_optional(url: str, dest: Path) -> bool:
    """Best-effort download: return False (leaving nothing behind) instead of dying on 404/error."""
    try:
        with urllib.request.urlopen(url) as resp, open(dest, "wb") as out:
            shutil.copyfileobj(resp, out)
        return True
    except Exception:  # noqa: BLE001 — optional file; its absence is not fatal
        dest.unlink(missing_ok=True)
        return False


def read_distribution_url(props_path: Path) -> str:
    """Read distributionUrl from gradle-wrapper.properties, unescaping the `\\:`."""
    for line in props_path.read_text(encoding="utf-8").splitlines():
        if line.startswith("distributionUrl="):
            return line.split("=", 1)[1].replace("\\:", ":").strip()
    return ""


# ── Bundling ─────────────────────────────────────────────────────────────────
@contextmanager
def bundle_stage(cfg: RtConfig):
    """Remove the bundle staging dir on exit — whether the command finished or
    was aborted — so leftovers from a previous run never leak into the next zip."""
    try:
        yield
    finally:
        shutil.rmtree(cfg.stage_dir, ignore_errors=True)


def make_bundle_zip(cfg: RtConfig) -> None:
    """Zip the staged bundle, with paths relative to stage_dir."""
    items = ["offline-repo", "gradle-dist", "offline.init.gradle.kts", "README-airgap.md"]
    with zipfile.ZipFile(cfg.output_zip, "w", zipfile.ZIP_DEFLATED) as zf:
        for item in items:
            path = cfg.stage_dir / item
            if path.is_dir():
                for child in sorted(path.rglob("*")):
                    if child.is_file():
                        zf.write(child, child.relative_to(cfg.stage_dir))
            elif path.is_file():
                zf.write(path, path.relative_to(cfg.stage_dir))
