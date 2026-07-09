"""The ``rt`` bundle commands must delete their staging folder on success *and*
on abort, so artifacts from a previous run never leak into the next zip."""

import importlib
import zipfile
from pathlib import Path

import pytest
import typer


@pytest.fixture
def rt(monkeypatch, tmp_path):
    """Import the rt modules with a throwaway config home (skips the first-run wizard)."""
    config_home = tmp_path / "cfg"
    (config_home / "solradm").mkdir(parents=True)
    (config_home / "solradm" / "settings.yaml").write_text("contexts:\n  available: []\n")
    monkeypatch.setenv("XDG_CONFIG_HOME", str(config_home))

    import solradm.config as cfg
    importlib.reload(cfg)

    from solradm.commands.rt import artifact, config, upload, util
    for mod in (config, util, artifact, upload):
        importlib.reload(mod)
    return artifact, config, upload, util


def _make_cfg(config_mod, tmp_path):
    return config_mod.build_config(
        project_dir=tmp_path / "proj",
        stage_dir=tmp_path / "stage",
        output_zip=tmp_path / "out.zip",
    )


# ── bundle_stage context manager ─────────────────────────────────────────────
def test_bundle_stage_removes_dir_on_success(rt, tmp_path):
    _, config, _, util = rt
    cfg = _make_cfg(config, tmp_path)
    with util.bundle_stage(cfg):
        cfg.stage_dir.mkdir(parents=True)
        (cfg.stage_dir / "leftover.txt").write_text("stale")
    assert not cfg.stage_dir.exists()


def test_bundle_stage_removes_dir_on_abort(rt, tmp_path):
    _, config, _, util = rt
    cfg = _make_cfg(config, tmp_path)
    with pytest.raises(KeyboardInterrupt):
        with util.bundle_stage(cfg):
            cfg.stage_dir.mkdir(parents=True)
            (cfg.stage_dir / "half-written.txt").write_text("partial")
            raise KeyboardInterrupt
    assert not cfg.stage_dir.exists()


# ── cmd_artifact ─────────────────────────────────────────────────────────────
class _FakeResolver:
    """Stand-in for TransitiveResolver that stages one file, no network."""
    fail = False

    def __init__(self, cfg, repo_url, with_sources):
        self.cfg = cfg
        self.file_count = 1
        self.warnings: list[str] = []
        self.fetch_failures: list[str] = []

    def resolve_and_fetch(self, roots, transitive):
        if self.fail:
            raise typer.Exit(1)
        (self.cfg.offline_repo_dir / "g" / "a" / "1").mkdir(parents=True, exist_ok=True)
        (self.cfg.offline_repo_dir / "g" / "a" / "1" / "a-1.jar").write_text("jar")


def test_cmd_artifact_cleans_stage_on_success(rt, tmp_path, monkeypatch):
    artifact, config, _, _ = rt
    cfg = _make_cfg(config, tmp_path)
    _FakeResolver.fail = False
    monkeypatch.setattr(artifact, "TransitiveResolver", _FakeResolver)
    monkeypatch.setattr(artifact, "make_bundle_zip",
                        lambda c: c.output_zip.write_bytes(b"zip"))

    artifact.cmd_artifact(cfg, ["g:a:1"], "https://repo", True, True)

    assert cfg.output_zip.exists()
    assert not cfg.stage_dir.exists()


def test_cmd_artifact_cleans_stage_on_abort(rt, tmp_path, monkeypatch):
    artifact, config, _, _ = rt
    cfg = _make_cfg(config, tmp_path)
    _FakeResolver.fail = True
    monkeypatch.setattr(artifact, "TransitiveResolver", _FakeResolver)

    with pytest.raises(typer.Exit):
        artifact.cmd_artifact(cfg, ["g:a:1"], "https://repo", True, True)

    assert not cfg.stage_dir.exists()


# ── cmd_upload ───────────────────────────────────────────────────────────────
def _make_bundle_zip(path: Path) -> None:
    with zipfile.ZipFile(path, "w") as zf:
        zf.writestr("offline-repo/g/a/1/a-1.jar", "jar")


def test_cmd_upload_cleans_temp_dir_on_failure(rt, tmp_path, monkeypatch):
    _, config, upload, _ = rt
    bundle = tmp_path / "bundle.zip"
    _make_bundle_zip(bundle)

    temp_extract = tmp_path / "extract"

    def _fake_mkdtemp():
        temp_extract.mkdir()
        return str(temp_extract)

    monkeypatch.setattr(upload.tempfile, "mkdtemp", _fake_mkdtemp)
    monkeypatch.setattr(upload.shutil, "which", lambda _: "/usr/bin/jf")

    def _boom(*a, **k):
        raise typer.Exit(1)
    monkeypatch.setattr(upload, "run", _boom)

    cfg = config.build_config(
        project_dir=tmp_path / "proj",
        artifactory_url="https://art",
        artifactory_token="tok",
    )

    with pytest.raises(typer.Exit):
        upload.cmd_upload(cfg, str(bundle))

    assert not temp_extract.exists()
