import sys
from typing import List

from solradm import main


class DummyApp:
    registered_groups: List[str] = []

    def __call__(self) -> None:
        pass


def test_completion_loads_command(monkeypatch):
    loaded = []

    def fake_load(name: str) -> None:
        loaded.append(name)

    monkeypatch.setattr(main, "_load_command", fake_load)
    monkeypatch.setattr(main, "app", DummyApp())
    monkeypatch.setenv("_SOLRADM_COMPLETE", "complete_bash")
    monkeypatch.setenv("COMP_WORDS", "solradm coll")
    monkeypatch.setenv("COMP_CWORD", "1")
    monkeypatch.setattr(sys, "argv", ["solradm"])

    main.run()

    assert loaded == ["coll"]
