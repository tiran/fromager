import collections
import logging
import os
import pathlib
import platform
import sys
import tempfile
import typing
import zipfile
from datetime import datetime

import elfdeps
from packaging.requirements import Requirement
from packaging.utils import parse_wheel_filename
from packaging.version import Version

from . import context, external_commands, overrides

logger = logging.getLogger(__name__)


class BuildEnvironment:
    "Wrapper for a virtualenv used for build isolation."

    def __init__(
        self,
        ctx: context.WorkContext,
        parent_dir: pathlib.Path,
        build_requirements: typing.Iterable[Requirement] | None,
    ):
        self._ctx = ctx
        self.path = parent_dir / f"build-{platform.python_version()}"
        self._build_requirements = list(build_requirements)
        self._build_requirements.append("build")
        self._createenv()

    @property
    def python(self) -> pathlib.Path:
        return (self.path / "bin/python3").absolute()

    def _createenv(self) -> None:
        if self.path.exists():
            logger.info("reusing build environment in %s", self.path)
            return

        logger.debug("creating build environment in %s", self.path)
        external_commands.run(
            [sys.executable, "-m", "virtualenv", str(self.path)],
            network_isolation=False,
        )
        logger.info("created build environment in %s", self.path)

        req_filename = self.path / "requirements.txt"
        # FIXME: Ensure each requirement is pinned to a specific version.
        with open(req_filename, "w") as f:
            if self._build_requirements:
                for r in self._build_requirements:
                    f.write(f"{r}\n")
        if not self._build_requirements:
            return
        external_commands.run(
            [
                str(self.python),
                "-m",
                "pip",
                "install",
                "--disable-pip-version-check",
                "--only-binary",
                ":all:",
            ]
            + self._ctx.pip_wheel_server_args
            + [
                "-r",
                str(req_filename.absolute()),
            ],
            cwd=str(self.path.parent),
            network_isolation=False,
        )
        logger.info("installed dependencies into build environment in %s", self.path)


def analyze_wheel_elfdeps(
    ctx: context.WorkContext, req: Requirement, wheel: pathlib.Path
) -> tuple[set[elfdeps.SOInfo], set[elfdeps.SOInfo]] | tuple[None, None]:
    """Analyze a wheel's ELF dependencies

    Logs and returns library dependencies and library provides
    """
    _, _, _, tags = parse_wheel_filename(wheel.name)
    if all(tag.platform == "all" for tag in tags):
        logger.debug("%s: %s is a purelib wheel", req.name, wheel)
        return None, None

    # mapping of required libraries to list of versions
    requires: set[elfdeps.SOInfo] = set()
    provides: set[elfdeps.SOInfo] = set()

    settings = elfdeps.ELFAnalyzeSettings(filter_soname=True)
    with zipfile.ZipFile(wheel) as zf:
        for zipinfo in zf.infolist():
            if zipinfo.filename.endswith(".so"):
                info = elfdeps.analyze_zipmember(zf, zipinfo, settings=settings)
                provides.update(info.provides)
                requires.update(info.requires)

    # Don't show provided names as required names
    requires = requires.difference(provides)

    if requires:
        reqmap: dict[str, list[str]] = collections.defaultdict(list)
        for r in requires:
            reqmap[r.soname].append(r.version)

        names = sorted(
            name for name in reqmap if not name.startswith(("ld-linux", "rtld"))
        )
        logger.info("%s: Requires libraries: %s", req.name, ", ".join(names))
        for name, versions in sorted(reqmap.items()):
            logger.debug(
                "%s: Requires %s(%s)",
                req.name,
                name,
                ", ".join(v for v in versions if v),
            )

    if provides:
        names = sorted(p.soname for p in provides)
        logger.info("%s: Provides libraries: %s", req.name, ", ".join(names))

    return requires, provides


def build_wheel(
    ctx: context.WorkContext,
    req: Requirement,
    sdist_root_dir: pathlib.Path,
    version: Version,
    build_env: BuildEnvironment,
) -> pathlib.Path | None:
    logger.info(
        f"{req.name}: building wheel for {req} in {sdist_root_dir} writing to {ctx.wheels_build}"
    )
    extra_environ = overrides.extra_environ_for_pkg(ctx.envs_dir, req.name, ctx.variant)
    # TODO: refactor?
    # Build Rust without network access
    extra_environ["CARGO_NET_OFFLINE"] = "true"
    # configure max jobs settings. should cover most of the cases, if not then the user can use ctx.jobs in their plugin
    if ctx.jobs:
        extra_environ["MAKEFLAGS"] = (
            f"{extra_environ.get('MAKEFLAGS', '')} -j{ctx.jobs}"
        )
        extra_environ["CMAKE_BUILD_PARALLEL_LEVEL"] = f"{ctx.jobs}"
        extra_environ["MAX_JOBS"] = f"{ctx.jobs}"

    # Start the timer
    start = datetime.now().replace(microsecond=0)
    overrides.find_and_invoke(
        req.name,
        "build_wheel",
        default_build_wheel,
        ctx=ctx,
        build_env=build_env,
        extra_environ=extra_environ,
        req=req,
        sdist_root_dir=sdist_root_dir,
        build_dir=ctx.settings.build_dir(req.name, sdist_root_dir),
        version=version,
    )
    # End the timer
    end = datetime.now().replace(microsecond=0)
    wheels = list(ctx.wheels_build.glob("*.whl"))
    if len(wheels) != 1:
        # TODO: raise error?
        return None
    wheel = wheels[0]
    logger.info(f"{req.name}: built wheel '{wheel}' in {end - start}")
    analyze_wheel_elfdeps(ctx, req, wheel)
    return wheel


def default_build_wheel(
    ctx: context.WorkContext,
    build_env: BuildEnvironment,
    extra_environ: dict[str, typing.Any],
    req: Requirement,
    sdist_root_dir: pathlib.Path,
    version: Version,
    build_dir: pathlib.Path,
) -> None:
    logger.debug(f"{req.name}: building wheel in {build_dir} with {extra_environ}")

    # Activate the virtualenv for the subprocess:
    # 1. Put the build environment at the front of the PATH to ensure
    #    any build tools are picked up from there and not global
    #    versions. If the caller has already set a path, start there.
    # 2. Set VIRTUAL_ENV so tools looking for that (for example,
    #    maturin) find it.
    existing_path = extra_environ.get("PATH") or os.environ.get("PATH") or ""
    path_parts = [str(build_env.python.parent)]
    if existing_path:
        path_parts.append(existing_path)
    updated_path = ":".join(path_parts)
    override_env = dict(os.environ)
    override_env.update(extra_environ)
    override_env["PATH"] = updated_path
    override_env["VIRTUAL_ENV"] = str(build_env.path)

    with tempfile.TemporaryDirectory() as dir_name:
        cmd = [
            os.fspath(build_env.python),
            "-m",
            "build",
            "--wheel",
            "-vvv",
            "--no-isolation",
            "--outdir",
            os.fspath(ctx.wheels_build),
            "-C=--build-option=--build-number=999rhelai",
            os.fspath(build_dir),
        ]
        external_commands.run(
            cmd,
            cwd=dir_name,
            extra_environ=override_env,
            network_isolation=ctx.network_isolation,
            log_filename=os.fspath(build_dir.parent / "build.log"),
        )
