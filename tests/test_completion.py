import importlib
import sys

import solradm.main as main_module


def test_completion_loads_subcommands(monkeypatch):
    main = importlib.reload(main_module)
    monkeypatch.setenv("SOLRADM_COMPLETE", "complete_bash")
    monkeypatch.setenv("COMP_WORDS", "solradm coll ")
    monkeypatch.setattr(sys, "argv", ["solradm"])
    try:
        main.run()
    except SystemExit:
        pass
    coll_info = next(info for info in main.app.registered_groups if info.name == "coll")
    import solradm.commands.collections as coll_module
    assert coll_info.typer_instance is coll_module.app
