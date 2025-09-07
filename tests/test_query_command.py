import importlib
from pathlib import Path

import pytest


def test_query_builds_params(monkeypatch, tmp_path, capsys):
    cfg_home = tmp_path / "cfg"
    monkeypatch.setenv("XDG_CONFIG_HOME", str(cfg_home))
    settings_dir = cfg_home / "solradm"
    settings_dir.mkdir(parents=True)
    (settings_dir / "settings.yaml").write_text("contexts:\n  current: {name: test}\n  available: []\n")

    from solradm.commands import collections as collections_module
    importlib.reload(collections_module)

    sent = {}

    async def fake_send_request(base, path, params=None):
        sent['base'] = base
        sent['path'] = path
        sent['params'] = params
        return {'response': {'docs': [{'id': '1', 'title': 'Doc'}]}}

    monkeypatch.setattr(collections_module, 'send_request', fake_send_request)
    monkeypatch.setattr(collections_module, 'get_nodes_by_role', lambda role: {"on": ['http://coord']} if role == 'coordinator' else {})
    monkeypatch.setattr(collections_module, 'get_overseer_leader', lambda: 'http://overseer')

    import asyncio

    asyncio.run(collections_module.query(
        'books',
        '*:*',
        rows=5,
        fl='id,title',
        start=10,
        fq=['type:book'],
        param=['facet=true', 'sort=score desc']
    ))

    assert sent['params']['q'] == '*:*'
    assert sent['params']['rows'] == 5
    assert sent['params']['fl'] == 'id,title'
    assert sent['params']['start'] == 10
    assert sent['params']['fq'] == ['type:book']
    assert sent['params']['facet'] == 'true'
    assert sent['params']['sort'] == 'score desc'
    assert sent['base'] == 'http://coord'

    out = capsys.readouterr().out
    # basic assertion that results were rendered
    assert 'Doc' in out


def test_query_falls_back_to_overseer(monkeypatch, tmp_path):
    cfg_home = tmp_path / "cfg"
    monkeypatch.setenv("XDG_CONFIG_HOME", str(cfg_home))
    settings_dir = cfg_home / "solradm"
    settings_dir.mkdir(parents=True)
    (settings_dir / "settings.yaml").write_text("contexts:\n  current: {name: test}\n  available: []\n")

    from solradm.commands import collections as collections_module
    importlib.reload(collections_module)

    sent = {}

    async def fake_send_request(base, path, params=None):
        sent['base'] = base
        sent['path'] = path
        sent['params'] = params
        return {'response': {'docs': []}}

    monkeypatch.setattr(collections_module, 'send_request', fake_send_request)
    monkeypatch.setattr(collections_module, 'get_nodes_by_role', lambda role: {"on": []})
    monkeypatch.setattr(collections_module, 'get_overseer_leader', lambda: 'http://overseer')

    import asyncio

    asyncio.run(collections_module.query('books', '*:*', rows=10, fl='*', start=0, fq=None, param=None, debug=False))

    assert sent['base'] == 'http://overseer'
