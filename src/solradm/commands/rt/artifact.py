"""``sa rt artifact`` — download Maven coordinates (+ transitive closure) into a bundle."""

from pathlib import Path
from typing import List, Optional

import typer

from solradm.commands.rt.config import RtConfig, build_config
from solradm.commands.rt.resolver import (
    MAVEN_CENTRAL,
    TransitiveResolver,
    parse_coordinate,
    prompt_coordinates,
)
from solradm.commands.rt.subapp import app
from solradm.commands.rt.util import bundle_stage, die, human_size, log, make_bundle_zip, warn


def cmd_artifact(cfg: RtConfig, coords: list[str], repo_url: str,
                 with_sources: bool, transitive: bool) -> None:
    coords = coords or prompt_coordinates()
    if not coords:
        die("No coordinates provided.")

    with bundle_stage(cfg):
        cfg.offline_repo_dir.mkdir(parents=True, exist_ok=True)
        resolver = TransitiveResolver(cfg, repo_url, with_sources)
        roots = [parse_coordinate(spec) for spec in coords]
        if transitive:
            log("Resolving transitive dependencies (compile + runtime scopes)…")
        resolver.resolve_and_fetch(roots, transitive)
        log(f"Staged {resolver.file_count} files into {cfg.offline_repo_dir}")

        if resolver.warnings:
            warn(f"{len(resolver.warnings)} resolution warning(s):")
            for w in resolver.warnings:
                warn(f"  {w}")
        if resolver.fetch_failures:
            warn(f"{len(resolver.fetch_failures)} artifact(s) could not be downloaded:")
            for f in resolver.fetch_failures:
                warn(f"  {f}")

        log(f"Zipping bundle -> {cfg.output_zip}")
        cfg.output_zip.unlink(missing_ok=True)
        make_bundle_zip(cfg)
        log(f"Done. Bundle: {cfg.output_zip} ({human_size(cfg.output_zip.stat().st_size)})")


@app.command(help="Download Maven artifacts by Gradle coordinate (no Gradle project needed), "
                  "with their transitive closure, and zip them.")
def artifact(
    coords: Optional[List[str]] = typer.Argument(
        None, metavar="group:artifact:version...",
        help="One or more Gradle implementation strings. If omitted, you'll be prompted."),
    repo: str = typer.Option(
        MAVEN_CENTRAL, "--repo",
        help=f"Maven repository base URL to download from (default: {MAVEN_CENTRAL})."),
    no_sources: bool = typer.Option(
        False, "--no-sources",
        help="Skip the -sources and -javadoc jars (fetch only the main artifact, pom, and .module)."),
    no_transitive: bool = typer.Option(
        False, "--no-transitive",
        help="Fetch only the named coordinates, not their transitive compile/runtime dependencies."),
    output: Path | None = typer.Option(
        None, "-o", "--output",
        help="Output bundle zip path (default: <project-dir>/monk-offline-deps-<date>.zip)."),
    stage_dir: Path | None = typer.Option(
        None, "--stage-dir", help="Bundle staging dir."),
):
    cfg = build_config(stage_dir=stage_dir, output_zip=output)
    cmd_artifact(cfg, coords or [], repo, not no_sources, not no_transitive)
