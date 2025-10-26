"""General utility functions for solradm."""
import os
import platform
import subprocess
from pathlib import Path

import rich
import typer


def open_directory(path: Path, select_file: bool = False) -> None:
    """
    Open a directory in the system file manager.

    Args:
        path: Path to open. If select_file is True, this should be a file path.
              Otherwise, this should be a directory path.
        select_file: If True, open the parent directory and select/highlight the file.
                    If False, open the directory directly.

    Raises:
        typer.Exit: If the operation fails
    """
    system = platform.system()

    try:
        if select_file:
            # Open directory with file selected/highlighted
            if system == "Windows":
                subprocess.run(["explorer", f"/select,{path}"], check=True)
            elif system == "Darwin":  # macOS
                subprocess.run(["open", "-R", str(path)], check=True)
            else:  # Linux and others
                # Linux xdg-open doesn't support file selection, open parent directory
                subprocess.run(["xdg-open", str(path.parent)], check=True)
        else:
            # Open directory directly
            path.mkdir(parents=True, exist_ok=True)

            if system == "Darwin":  # macOS
                subprocess.run(["open", str(path)], check=True)
            elif system == "Linux":
                subprocess.run(["xdg-open", str(path)], check=True)
            elif system == "Windows":
                os.startfile(path)
            else:
                rich.print(f"[yellow]⚠  Cannot open directory automatically on {system}")
                rich.print(f"[yellow]   Path: {path}")
                raise typer.Exit(0)

        if select_file:
            rich.print(f"[success]✅  Opened location of {path.name}")
        else:
            rich.print(f"[success]✅  Opened directory: {path}")

    except subprocess.CalledProcessError as e:
        rich.print(f"[error] ❌ Failed to open location: {e}")
        rich.print(f"[yellow]   Path: {path}")
        raise typer.Exit(1)
    except Exception as e:
        rich.print(f"[error] ❌ Failed to open location: {e}")
        rich.print(f"[yellow]   Path: {path}")
        raise typer.Exit(1)
