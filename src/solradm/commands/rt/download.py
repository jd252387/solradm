"""``sa rt download`` — resolve a Gradle project's full dependency closure into a bundle.

Unlike the original script (which assumed it lived inside the Gradle repo), the project
root is supplied explicitly via ``--project-dir`` and the Gradle init scripts are loaded
from this package's bundled ``resources/`` directory.
"""

import shutil
from datetime import datetime, timezone
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
from solradm.commands.rt.util import (
    bundle_stage,
    die,
    download,
    human_size,
    log,
    make_bundle_zip,
    read_distribution_url,
    run,
)

_ART = artifactory_defaults()
RESOURCES_DIR = Path(__file__).resolve().parent / "resources"


def run_gradle_resolution(cfg: RtConfig, skip_tests: bool) -> None:
    gradle_args = [
        "-g", cfg.offline_guh,
        "-I", RESOURCES_DIR / "download-offline.init.gradle.kts",
        "--refresh-dependencies",
        "--no-configuration-cache",
        "quarkusGenerateDevAppModel",
        "resolveOfflineDependencies",
    ]
    if skip_tests:
        gradle_args += ["-x", "test"]
        log("Resolving dependencies WITHOUT running tests (some test-time deps may be missed).")
    log(f"Populating isolated Gradle cache at {cfg.offline_guh}")
    run([cfg.project_dir / "gradlew", *gradle_args], cwd=cfg.project_dir)


def harvest_cache(cfg: RtConfig) -> None:
    files_root = cfg.offline_guh / "caches" / "modules-2" / "files-2.1"
    if not files_root.is_dir():
        die(f"Gradle cache not found at {files_root} (did the resolution step run?)")

    log(f"Harvesting cache into Maven layout: {cfg.offline_repo_dir}")
    if cfg.offline_repo_dir.exists():
        shutil.rmtree(cfg.offline_repo_dir)
    cfg.offline_repo_dir.mkdir(parents=True, exist_ok=True)

    count = 0
    for src in files_root.rglob("*"):
        if not src.is_file():
            continue
        # Cache layout: files-2.1/<group>/<artifact>/<version>/<sha1>/<filename>
        parts = src.relative_to(files_root).parts
        if len(parts) < 5:
            continue
        group, artifact, version = parts[0], parts[1], parts[2]
        filename = Path(*parts[4:])  # strip the <sha1>/ segment
        dest_dir = cfg.offline_repo_dir.joinpath(*group.split("."), artifact, version)
        dest_dir.mkdir(parents=True, exist_ok=True)
        dest = dest_dir / filename
        if not dest.exists():  # cp -n: never clobber
            shutil.copyfile(src, dest)
        count += 1

    if count == 0:
        die("No artifacts harvested — cache appears empty.")
    log(f"Harvested {count} files.")


def fetch_gradle_dist(cfg: RtConfig) -> None:
    cfg.gradle_dist_dir.mkdir(parents=True, exist_ok=True)
    props = cfg.project_dir / "gradle" / "wrapper" / "gradle-wrapper.properties"
    url = read_distribution_url(props)
    if not url:
        die(f"Could not read distributionUrl from {props}")
    log(f"Downloading Gradle distribution: {url}")
    download(url, cfg.gradle_dist_dir / cfg.gradle_dist_file)


def write_readme(cfg: RtConfig) -> None:
    generated = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    readme = f"""# monk offline dependency bundle

Generated {generated} for Gradle {cfg.gradle_version}.

Contents:
- `offline-repo/`            Maven-layout repository (jars, poms, .module, -sources, -javadoc, plugin markers)
- `gradle-dist/{cfg.gradle_dist_file}`  Gradle distribution
- `offline.init.gradle.kts`  Init script that repoints Gradle at Artifactory

## 1. Upload into Artifactory (run inside the air-gapped network)

Create two Artifactory repos: a **Maven** repo (e.g. `{cfg.artifactory_maven_repo}`) and a
**generic** repo (e.g. `{cfg.artifactory_generic_repo}`). Then (configure credentials once
via `sa rt setup`, or pass them as flags):

    sa rt upload --url https://artifactory.internal/artifactory --token <access-token> .
    # or, with a configured profile: sa rt upload monk-offline-deps-<date>.zip

## 2. Point the build at Artifactory

a) Wrapper distribution — set in `gradle/wrapper/gradle-wrapper.properties`:

    distributionUrl=https\\://artifactory.internal/artifactory/{cfg.artifactory_generic_repo}/gradle/distributions/{cfg.gradle_dist_file}

b) Dependencies + plugins — build with the bundled init script (do NOT pass --offline):

    export ARTIFACTORY_MAVEN_URL="https://artifactory.internal/artifactory/{cfg.artifactory_maven_repo}"
    ./gradlew -I offline.init.gradle.kts build

## Prerequisite: JDK 25

The build uses a Java 25 toolchain. This project configures no toolchain download resolver,
so Gradle will NOT try to fetch a JDK — it requires JDK 25 to already be installed on the
machine. Ensure a JDK 25 is present (and discoverable, e.g. via JAVA_HOME or
`org.gradle.java.installations.paths`).
"""
    (cfg.stage_dir / "README-airgap.md").write_text(readme, encoding="utf-8")


def cmd_download(cfg: RtConfig, skip_tests: bool) -> None:
    with bundle_stage(cfg):
        cfg.stage_dir.mkdir(parents=True, exist_ok=True)
        run_gradle_resolution(cfg, skip_tests)
        harvest_cache(cfg)
        fetch_gradle_dist(cfg)
        shutil.copyfile(
            RESOURCES_DIR / "offline.init.gradle.kts",
            cfg.stage_dir / "offline.init.gradle.kts",
        )
        write_readme(cfg)

        log(f"Zipping bundle -> {cfg.output_zip}")
        cfg.output_zip.unlink(missing_ok=True)
        make_bundle_zip(cfg)
        log(f"Done. Bundle: {cfg.output_zip} ({human_size(cfg.output_zip.stat().st_size)})")


@app.command(help="Resolve all dependencies of a Gradle project and produce the offline bundle zip.")
def download(
    project_dir: Path | None = typer.Option(
        None, "--project-dir",
        help="Gradle project root (contains gradlew + gradle/wrapper). Default: current directory."),
    no_tests: bool = typer.Option(
        False, "--no-tests",
        help="Skip test execution (faster; may miss some test-time deps)."),
    gradle_version: str = typer.Option(
        DEFAULT_GRADLE_VERSION, "--gradle-version",
        help="Gradle version of the wrapper distribution (used for the dist filename)."),
    maven_repo: str = typer.Option(
        _ART.get("maven_repo", DEFAULT_MAVEN_REPO), "--maven-repo",
        help="Target Maven repo key (referenced in the generated README)."),
    generic_repo: str = typer.Option(
        _ART.get("generic_repo", DEFAULT_GENERIC_REPO), "--generic-repo",
        help="Target generic repo key (referenced in the generated README)."),
    stage_dir: Path | None = typer.Option(
        None, "--stage-dir", help="Bundle staging dir (default: <project-dir>/build/offline-bundle)."),
    offline_guh: Path | None = typer.Option(
        None, "--offline-guh",
        help="Isolated Gradle user home (default: <project-dir>/.offline-gradle-home)."),
    output: Path | None = typer.Option(
        None, "-o", "--output",
        help="Output bundle zip path (default: <project-dir>/monk-offline-deps-<date>.zip)."),
):
    cfg = build_config(
        project_dir=project_dir,
        gradle_version=gradle_version,
        stage_dir=stage_dir,
        offline_guh=offline_guh,
        output_zip=output,
        artifactory_maven_repo=maven_repo,
        artifactory_generic_repo=generic_repo,
    )
    cmd_download(cfg, no_tests)
