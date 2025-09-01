from pathlib import Path
import importlib
import sys


def _prepare_main(monkeypatch, tmp_path: Path):
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path))
    config_dir = tmp_path / "solradm"
    config_dir.mkdir(parents=True)
    (config_dir / "settings.yaml").write_text("contexts:\n  available: []\n  current: {name: test}\n")
    import solradm.main as main
    importlib.reload(main)
    return main


def test_switch_shortcut(monkeypatch, tmp_path):
    main = _prepare_main(monkeypatch, tmp_path)
    called = {}

    def fake_switch(name: str):
        called['name'] = name

    monkeypatch.setattr(main.config, 'switch', fake_switch)
    monkeypatch.setattr(sys, 'argv', ['sa', 'myctx'])
    main.run()
    assert called['name'] == 'myctx'


def test_subcommand_no_shortcut(monkeypatch, tmp_path):
    main = _prepare_main(monkeypatch, tmp_path)
    called = {}

    def fake_switch(name: str):
        called['name'] = name

    monkeypatch.setattr(main.config, 'switch', fake_switch)
    invoked = {}

    def fake_app():
        invoked['called'] = True

    monkeypatch.setattr(main, 'app', fake_app)
    monkeypatch.setattr(sys, 'argv', ['sa', 'context', '--help'])
    main.run()
    assert invoked.get('called')
    assert 'name' not in called
