"""``sa rt upload`` — push a built bundle into Artifactory via the JFrog CLI (jf)."""

import shutil
import tempfile
import zipfile
from pathlib import Path

import typer

from solradm.commands.rt.config import (
    DEFAULT_GENERIC_REPO,
    DEFAULT_GRADLE_VERSION,
    DEFAULT_MAVEN_REPO,
    RtConfig,
    artifactory_defaults,
    build_config,
)
from solradm.commands.rt.subapp import app
from solradm.commands.rt.util import die, log, run

_ART = artifactory_defaults()


def build_auth(cfg: RtConfig) -> list:
    if cfg.jfrog_server_id:
        return [f"--server-id={cfg.jfrog_server_id}"]

    if not cfg.artifactory_url:
        die("Set --url or --server-id (or configure an Artifactory profile via `sa rt setup`).")
    auth = [f"--url={cfg.artifactory_url}"]
    if cfg.artifactory_token:
        auth.append(f"--access-token={cfg.artifactory_token}")
    elif cfg.artifactory_user:
        auth += [f"--user={cfg.artifactory_user}", f"--password={cfg.artifactory_password}"]
    else:
        die("Set --token, or --user + --password.")
    return auth


def cmd_upload(cfg: RtConfig, bundle: str | None) -> None:
    cleanup: Path | None = None

    if bundle and Path(bundle).is_file():
        workdir = Path(tempfile.mkdtemp())
        cleanup = workdir
        log(f"Expanding {bundle} -> {workdir}")
        with zipfile.ZipFile(bundle) as zf:
            zf.extractall(workdir)
    elif bundle and Path(bundle).is_dir():
        workdir = Path(bundle)
    elif cfg.stage_dir.is_dir():
        workdir = cfg.stage_dir
    else:
        die(f"Provide a bundle zip or directory (or run from a machine that has {cfg.stage_dir}).")

    if shutil.which("jf") is None:
        die("jf (JFrog CLI) not found on PATH.")
    if not (workdir / "offline-repo").is_dir():
        die(f"offline-repo/ not found in {workdir}")

    auth = build_auth(cfg)

    log(f"Uploading Maven artifacts -> {cfg.artifactory_maven_repo}")
    run(
        ["jf", "rt", "upload", *auth, "offline-repo/(**)", f"{cfg.artifactory_maven_repo}/{{1}}"],
        cwd=workdir,
    )

    dist = workdir / "gradle-dist" / cfg.gradle_dist_file
    if dist.is_file():
        log(f"Uploading Gradle distribution -> {cfg.artifactory_generic_repo}")
        run(
            ["jf", "rt", "upload", *auth,
             f"gradle-dist/{cfg.gradle_dist_file}",
             f"{cfg.artifactory_generic_repo}/gradle/distributions/"],
            cwd=workdir,
        )

    if cleanup is not None:
        shutil.rmtree(cleanup)
    log("Upload complete.")


@app.command(help="Upload a previously built bundle into Artifactory via the JFrog CLI (jf).")
def upload(
    bundle: str | None = typer.Argument(
        None, help="Bundle zip or directory to upload (default: --stage-dir)."),
    url: str = typer.Option(
        _ART.get("url", ""), "--url",
        help="Base Artifactory URL, e.g. https://artifactory.internal/artifactory."),
    maven_repo: str = typer.Option(
        _ART.get("maven_repo", DEFAULT_MAVEN_REPO), "--maven-repo",
        help="Target Maven repo key."),
    generic_repo: str = typer.Option(
        _ART.get("generic_repo", DEFAULT_GENERIC_REPO), "--generic-repo",
        help="Target generic repo key for the Gradle distribution."),
    token: str = typer.Option(
        _ART.get("token", ""), "--token",
        help="Artifactory access token (preferred)."),
    user: str = typer.Option(
        "", "--user", help="Artifactory username (used with --password when no token)."),
    password: str = typer.Option(
        "", "--password", help="Artifactory password (used with --user)."),
    server_id: str = typer.Option(
        "", "--server-id",
        help="Use a preconfigured 'jf' server-id instead of URL/credentials."),
    gradle_version: str = typer.Option(
        DEFAULT_GRADLE_VERSION, "--gradle-version",
        help="Gradle version (determines the distribution filename to upload)."),
    stage_dir: Path | None = typer.Option(
        None, "--stage-dir",
        help="Bundle staging dir to fall back to when no bundle argument is given."),
):
    cfg = build_config(
        gradle_version=gradle_version,
        stage_dir=stage_dir,
        artifactory_url=url,
        artifactory_maven_repo=maven_repo,
        artifactory_generic_repo=generic_repo,
        artifactory_token=token,
        artifactory_user=user,
        artifactory_password=password,
        jfrog_server_id=server_id,
    )
    cmd_upload(cfg, bundle)
