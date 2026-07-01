"""Configuration for the ``rt`` (Artifactory) subcommands.

Replaces the env-var-driven ``Config``/``load_config`` from the standalone
``airgap/offline_deps.py``: every value now arrives as a Typer option, and the
Artifactory connection details default from solradm's persisted ``artifactory``
config block (see ``artifactory_defaults``).
"""

from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

from solradm.config import settings

DEFAULT_GRADLE_VERSION = "9.5.0"
DEFAULT_MAVEN_REPO = "monk-offline-maven"
DEFAULT_GENERIC_REPO = "monk-offline-generic"


@dataclass
class RtConfig:
    gradle_version: str
    gradle_dist_file: str
    offline_guh: Path           # isolated Gradle home: cache holds ONLY this project's deps
    stage_dir: Path             # bundle staging dir
    offline_repo_dir: Path      # Maven-layout repo inside the bundle
    gradle_dist_dir: Path       # holds the Gradle distribution zip
    output_zip: Path            # final bundle zip
    project_dir: Path           # Gradle project root (was REPO_ROOT)
    artifactory_url: str
    artifactory_maven_repo: str
    artifactory_generic_repo: str
    artifactory_token: str
    artifactory_user: str
    artifactory_password: str
    jfrog_server_id: str


def artifactory_defaults() -> dict:
    """Return the persisted ``artifactory`` config block as a plain dict (empty if unset).

    Used to seed the Typer option defaults so ``sa rt upload`` works with no flags once
    the profile has been configured via the setup wizard or ``sa rt setup``.
    """
    art = settings.get("artifactory")
    if not art:
        return {}
    return art.to_dict() if hasattr(art, "to_dict") else dict(art)


def build_config(
    *,
    project_dir: Path | None = None,
    gradle_version: str = DEFAULT_GRADLE_VERSION,
    stage_dir: Path | None = None,
    offline_guh: Path | None = None,
    output_zip: Path | None = None,
    artifactory_url: str = "",
    artifactory_maven_repo: str = DEFAULT_MAVEN_REPO,
    artifactory_generic_repo: str = DEFAULT_GENERIC_REPO,
    artifactory_token: str = "",
    artifactory_user: str = "",
    artifactory_password: str = "",
    jfrog_server_id: str = "",
) -> RtConfig:
    project_dir = (project_dir or Path.cwd()).resolve()
    stage_dir = stage_dir or (project_dir / "build" / "offline-bundle")
    today = datetime.now().strftime("%Y%m%d")
    return RtConfig(
        gradle_version=gradle_version,
        gradle_dist_file=f"gradle-{gradle_version}-bin.zip",
        offline_guh=offline_guh or (project_dir / ".offline-gradle-home"),
        stage_dir=stage_dir,
        offline_repo_dir=stage_dir / "offline-repo",
        gradle_dist_dir=stage_dir / "gradle-dist",
        output_zip=output_zip or (project_dir / f"monk-offline-deps-{today}.zip"),
        project_dir=project_dir,
        artifactory_url=artifactory_url,
        artifactory_maven_repo=artifactory_maven_repo,
        artifactory_generic_repo=artifactory_generic_repo,
        artifactory_token=artifactory_token,
        artifactory_user=artifactory_user,
        artifactory_password=artifactory_password,
        jfrog_server_id=jfrog_server_id,
    )
