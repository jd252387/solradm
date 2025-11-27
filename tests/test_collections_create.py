import asyncio
import sys
from pathlib import Path

import pytest


def test_create_populate_includes_confirmation(monkeypatch, tmp_path):
    """Ensure create passes skip_checks=False so populate shows confirmation."""

    async def run_test() -> None:
        config_path = Path(tmp_path) / "solradm" / "settings.yaml"
        config_path.parent.mkdir(parents=True, exist_ok=True)
        config_path.write_text("contexts:\n  available: []\n  current: {name: default}\n")

        monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path))

        for module in [
            "solradm.config",
            "solradm.commands.filters.utils",
            "solradm.commands.collections.lifecycle",
        ]:
            sys.modules.pop(module, None)

        from solradm.commands.collections import lifecycle

        async def fake_send_request(*args, **kwargs):
            return None

        monkeypatch.setattr(lifecycle, "send_request", fake_send_request)
        monkeypatch.setattr(lifecycle, "get_overseer_leader", lambda: "leader")

        captured_kwargs: dict[str, object] = {}

        async def fake_populate(**kwargs):
            captured_kwargs.update(kwargs)

        monkeypatch.setattr(lifecycle, "populate", fake_populate)

        await lifecycle.create(
            dry_run=False,
            name="search",
            shards=2,
            conf="configset",
            upload_conf=None,
            populate_after=True,
            node=None,
            node_order="numerical",
        )

        assert captured_kwargs["skip_checks"] is False

    asyncio.run(run_test())
