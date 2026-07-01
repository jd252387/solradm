"""Maven coordinate fetching and transitive-closure resolution for ``rt artifact``.

Ported verbatim (behaviour-wise) from ``airgap/offline_deps.py``. A pragmatic Maven
POM walker: enough to mirror the compile+runtime closure of a coordinate the way Gradle
puts it on a classpath. It honours parent POMs, property substitution,
``<dependencyManagement>`` (including imported BOMs) and scope/optional filtering.

It deliberately does NOT implement Maven's full nearest-wins conflict resolution or
``<exclusions>``: for an offline mirror it is safe — and desirable — to over-fetch, so
when several versions of a module are reachable we simply mirror them all.
"""

import xml.etree.ElementTree as ET
from collections import deque
from dataclasses import dataclass
from pathlib import Path

from solradm.commands.rt.config import RtConfig
from solradm.commands.rt.util import die, download, download_optional, log, warn

MAVEN_CENTRAL = "https://repo1.maven.org/maven2"


@dataclass
class Coordinate:
    """A Maven coordinate parsed from Gradle notation: group:artifact:version[:classifier][@ext]."""
    group: str
    artifact: str
    version: str
    classifier: str | None
    extension: str

    @property
    def path(self) -> str:  # group/with/slashes/artifact/version
        return "/".join([*self.group.split("."), self.artifact, self.version])

    def filename(self, classifier: str | None = None, extension: str | None = None) -> str:
        suffix = f"-{classifier}" if classifier else ""
        return f"{self.artifact}-{self.version}{suffix}.{extension or self.extension}"

    def __str__(self) -> str:
        spec = f"{self.group}:{self.artifact}:{self.version}"
        if self.classifier:
            spec += f":{self.classifier}"
        return spec + (f"@{self.extension}" if self.extension != "jar" else "")


def parse_coordinate(spec: str) -> Coordinate:
    spec = spec.strip()
    extension = "jar"
    if "@" in spec:
        spec, extension = spec.rsplit("@", 1)
    parts = spec.split(":")
    if len(parts) < 3 or not all(parts[:3]):
        die(f"Invalid coordinate '{spec}': expected group:artifact:version[:classifier][@ext]")
    classifier = parts[3] if len(parts) >= 4 and parts[3] else None
    return Coordinate(parts[0], parts[1], parts[2], classifier, extension)


def fetch_artifact(cfg: RtConfig, coord: Coordinate, repo_url: str, with_sources: bool,
                   required: bool = True, failures: list[str] | None = None) -> int:
    """Download one coordinate into the Maven-layout offline-repo. Returns the file count.

    The main artifact is required for explicitly-requested coordinates (``required=True``)
    and best-effort for transitively-discovered ones — a missing transitive jar is recorded
    in ``failures`` rather than fatal. The pom, .module, and -sources/-javadoc jars are
    always best-effort.
    """
    base = f"{repo_url.rstrip('/')}/{coord.path}"
    dest_dir = cfg.offline_repo_dir.joinpath(*coord.group.split("."), coord.artifact, coord.version)
    dest_dir.mkdir(parents=True, exist_ok=True)

    main = coord.filename(classifier=coord.classifier)
    main_dest = dest_dir / main
    if main_dest.exists():
        log(f"  {main} already present")
    elif required:
        log(f"  {main}")
        download(f"{base}/{main}", main_dest)
    elif download_optional(f"{base}/{main}", main_dest):
        log(f"  {main}")
    else:
        if failures is not None:
            failures.append(str(coord))
        return 0
    count = 1

    optionals = [] if coord.extension == "pom" else [coord.filename(extension="pom")]
    optionals.append(coord.filename(extension="module"))
    if with_sources:
        optionals += [coord.filename(classifier="sources", extension="jar"),
                      coord.filename(classifier="javadoc", extension="jar")]

    for name in optionals:
        dest = dest_dir / name
        if dest.exists():
            count += 1
        elif download_optional(f"{base}/{name}", dest):
            log(f"  {name}")
            count += 1

    return count


def prompt_coordinates() -> list[str]:
    log("Enter Gradle implementation strings (group:artifact:version), one per line; blank line to finish:")
    coords: list[str] = []
    try:
        while True:
            line = input("  > ").strip()
            if not line:
                break
            coords.append(line)
    except EOFError:
        pass
    return coords


# ── transitive dependency resolution ─────────────────────────────────────────
_RUNTIME_SCOPES = {"compile", "runtime"}
_TYPE_EXTENSIONS = {"bundle": "jar", "maven-plugin": "jar", "ejb": "jar", "test-jar": "jar"}


def _ext_for(packaging: str | None) -> str:
    """Map a Maven dependency <type> to the file extension actually published."""
    return _TYPE_EXTENSIONS.get(packaging or "jar", packaging or "jar")


def _local(tag: str) -> str:
    """Strip the XML namespace from an ElementTree tag ('{ns}foo' -> 'foo')."""
    return tag.rsplit("}", 1)[-1]


def _child(elem, name: str):
    if elem is None:
        return None
    for c in list(elem):
        if _local(c.tag) == name:
            return c
    return None


def _child_text(elem, name: str) -> str | None:
    c = _child(elem, name)
    if c is not None and c.text and c.text.strip():
        return c.text.strip()
    return None


def _children(elem, name: str) -> list:
    if elem is None:
        return []
    return [c for c in list(elem) if _local(c.tag) == name]


@dataclass
class Dependency:
    group: str
    artifact: str
    version: str | None
    classifier: str | None
    type: str
    scope: str
    optional: bool


@dataclass
class EffectivePom:
    group: str
    artifact: str
    version: str
    properties: dict
    managed: dict          # (group, artifact) -> version
    dependencies: list     # list[Dependency]


class TransitiveResolver:
    """Walks POMs to mirror the full compile/runtime dependency closure of some roots."""

    def __init__(self, cfg: RtConfig, repo_url: str, with_sources: bool):
        self.cfg = cfg
        self.repo_url = repo_url.rstrip("/")
        self.with_sources = with_sources
        self._elem_cache: dict[tuple, object] = {}
        self._eff_cache: dict[tuple, EffectivePom | None] = {}
        self.global_managed: dict[tuple, str] = {}   # versions managed by the root BOMs
        self.file_count = 0
        self.fetch_failures: list[str] = []
        self.warnings: list[str] = []

    # -- POM loading & effective-model construction ---------------------------
    def _pom_url(self, g, a, v) -> str:
        return f"{self.repo_url}/{'/'.join(g.split('.'))}/{a}/{v}/{a}-{v}.pom"

    def _pom_path(self, g, a, v) -> Path:
        return self.cfg.offline_repo_dir.joinpath(*g.split("."), a, v, f"{a}-{v}.pom")

    def _load_elem(self, g, a, v):
        key = (g, a, v)
        if key in self._elem_cache:
            return self._elem_cache[key]
        dest = self._pom_path(g, a, v)
        if not dest.exists():
            dest.parent.mkdir(parents=True, exist_ok=True)
            if not download_optional(self._pom_url(g, a, v), dest):
                self.warnings.append(f"POM not found: {g}:{a}:{v}")
                self._elem_cache[key] = None
                return None
        try:
            elem = ET.parse(dest).getroot()
        except ET.ParseError as exc:
            self.warnings.append(f"Unparseable POM {g}:{a}:{v}: {exc}")
            elem = None
        self._elem_cache[key] = elem
        return elem

    def _resolve_props(self, value: str | None, props: dict) -> str | None:
        """Substitute ${...} placeholders, leaving unknown ones untouched."""
        if value is None or "${" not in value:
            return value
        out = value
        for _ in range(20):
            start = out.find("${")
            if start == -1:
                break
            end = out.find("}", start)
            if end == -1:
                break
            repl = props.get(out[start + 2:end])
            if repl is None:
                break
            out = out[:start] + repl + out[end + 1:]
        return out

    def _effective(self, g, a, v) -> EffectivePom | None:
        """Build the effective POM for g:a:v (parent-merged, properties resolved, BOMs imported)."""
        key = (g, a, v)
        if key in self._eff_cache:
            return self._eff_cache[key]
        self._eff_cache[key] = None      # cycle guard for self-referential parents/imports
        elem = self._load_elem(g, a, v)
        if elem is None:
            return None

        parent_eff = None
        parent = _child(elem, "parent")
        if parent is not None:
            pg, pa, pv = (_child_text(parent, "groupId"),
                          _child_text(parent, "artifactId"),
                          _child_text(parent, "version"))
            if pg and pa and pv:
                parent_eff = self._effective(pg, pa, pv)

        group = _child_text(elem, "groupId") or (parent_eff.group if parent_eff else g)
        version = _child_text(elem, "version") or (parent_eff.version if parent_eff else v)
        artifact = _child_text(elem, "artifactId") or a

        props = dict(parent_eff.properties) if parent_eff else {}
        props_elem = _child(elem, "properties")
        if props_elem is not None:
            for p in list(props_elem):
                props[_local(p.tag)] = (p.text or "").strip()
        # Reserved project.* aliases always reflect THIS pom.
        props.update({
            "project.groupId": group, "pom.groupId": group, "groupId": group,
            "project.artifactId": artifact, "pom.artifactId": artifact, "artifactId": artifact,
            "project.version": version, "pom.version": version, "version": version,
        })
        if parent_eff:
            props["project.parent.version"] = parent_eff.version
            props["project.parent.groupId"] = parent_eff.group

        # dependencyManagement: parent's, then this pom's (overriding), then imported BOMs (filling gaps).
        managed = dict(parent_eff.managed) if parent_eff else {}
        imports: list[tuple] = []
        dm_deps = _child(_child(elem, "dependencyManagement"), "dependencies")
        if dm_deps is not None:
            for d in _children(dm_deps, "dependency"):
                dg = self._resolve_props(_child_text(d, "groupId"), props)
                da = self._resolve_props(_child_text(d, "artifactId"), props)
                dv = self._resolve_props(_child_text(d, "version"), props)
                if _child_text(d, "scope") == "import" and _child_text(d, "type") == "pom":
                    if dg and da and dv:
                        imports.append((dg, da, dv))
                elif dg and da and dv:
                    managed[(dg, da)] = dv
        for ig, ia, iv in imports:
            imported = self._effective(ig, ia, iv)
            if imported:
                for mk, mv in imported.managed.items():
                    managed.setdefault(mk, mv)

        dependencies: list[Dependency] = []
        deps_elem = _child(elem, "dependencies")
        if deps_elem is not None:
            for d in _children(deps_elem, "dependency"):
                dg = self._resolve_props(_child_text(d, "groupId"), props)
                da = self._resolve_props(_child_text(d, "artifactId"), props)
                if not dg or not da:
                    continue
                dv = self._resolve_props(_child_text(d, "version"), props) or managed.get((dg, da))
                dependencies.append(Dependency(
                    group=dg, artifact=da, version=dv,
                    classifier=self._resolve_props(_child_text(d, "classifier"), props),
                    type=_child_text(d, "type") or "jar",
                    scope=_child_text(d, "scope") or "compile",
                    optional=(_child_text(d, "optional") or "false").lower() == "true",
                ))

        eff = EffectivePom(group, artifact, version, props, managed, dependencies)
        self._eff_cache[key] = eff
        return eff

    # -- fetching -------------------------------------------------------------
    def _fetch(self, dep: Dependency, required: bool) -> None:
        coord = Coordinate(dep.group, dep.artifact, dep.version,
                           dep.classifier, _ext_for(dep.type))
        self.file_count += fetch_artifact(
            self.cfg, coord, self.repo_url, self.with_sources,
            required=required, failures=self.fetch_failures,
        )

    def resolve_and_fetch(self, roots: list[Coordinate], transitive: bool) -> None:
        visited: set[tuple] = set()
        queue: deque[Dependency] = deque()

        for c in roots:
            log(f"Fetching {c} from {self.repo_url}")
            root = Dependency(c.group, c.artifact, c.version, c.classifier, c.extension, "compile", False)
            self._fetch(root, required=True)
            visited.add((c.group, c.artifact, c.version, c.classifier, c.extension))
            if transitive:
                eff = self._effective(c.group, c.artifact, c.version)
                if eff:   # let the roots' own dependencyManagement govern unversioned transitives
                    for mk, mv in eff.managed.items():
                        self.global_managed.setdefault(mk, mv)
                queue.append(root)

        while queue:
            dep = queue.popleft()
            if dep.version is None:
                continue
            eff = self._effective(dep.group, dep.artifact, dep.version)
            if eff is None:
                continue
            for child in eff.dependencies:
                if child.scope not in _RUNTIME_SCOPES or child.optional:
                    continue
                version = child.version or self.global_managed.get((child.group, child.artifact))
                if not version:
                    self.warnings.append(
                        f"Unresolved version for {child.group}:{child.artifact} "
                        f"(required by {dep.group}:{dep.artifact}:{dep.version})")
                    continue
                ident = (child.group, child.artifact, version, child.classifier, _ext_for(child.type))
                if ident in visited:
                    continue
                visited.add(ident)
                resolved = Dependency(child.group, child.artifact, version,
                                      child.classifier, child.type, child.scope, False)
                log(f"Fetching {child.group}:{child.artifact}:{version} (via {dep.artifact})")
                self._fetch(resolved, required=False)
                queue.append(resolved)
