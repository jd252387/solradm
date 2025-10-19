from pathlib import Path
from async_typer import AsyncTyper
import runpy
from solradm.commands.callbacks import add_verbosity_option

app = AsyncTyper()
add_verbosity_option(app)

def load_all_handlers(package_dir: Path):
    for file in package_dir.glob("*.py"):
        if file.name == "__init__.py":
            continue
    runpy.run_path(str(file))

load_all_handlers(Path(__file__).parent)