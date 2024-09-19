import importlib.metadata
import logging
import pathlib
import platform
import re
import subprocess
import sys
import typing

from packaging.requirements import Requirement

from . import (
    context,
    dependencies,
    external_commands,
    resolver,
)
from .requirements_file import RequirementType

logger = logging.getLogger(__name__)

# Pip has no API, so parse its output looking for what it couldn't install.
# Verbose regular expressions ignore blank spaces, so we have to escape those to
# have them recognized as being part of the pattern.
_pip_missing_dependency_pattern = re.compile(
    r"""(
    Could\ not\ find\ a\ version\ that\ satisfies\ the\ requirement\ (\w+)
    |
    No\ matching\ distribution\ found\ for\ (\w+)
    |
    ResolutionImpossible  # usually when constraints prevent a match
    )""",
    flags=re.VERBOSE,
)


class MissingDependency(Exception):  # noqa: N818
    def __init__(
        self,
        ctx: context.WorkContext,
        req_type: RequirementType,
        req: Requirement | str | None,
        all_reqs: typing.Iterable[Requirement],
    ):
        self.all_reqs = all_reqs
        resolutions = []
        for r in all_reqs:
            try:
                _, version = resolver.resolve(ctx, r, resolver.PYPI_SERVER_URL)
            except Exception as err:
                resolutions.append(f"{r} -> {err}")
            else:
                resolutions.append(f"{r} -> {version}")
        formatted_reqs = "\n".join(resolutions)
        if req:
            msg = (
                f"Failed to install {req_type} dependency {req}. "
                f"Check all {req_type} dependencies:\n{formatted_reqs}"
            )
        else:
            msg = (
                f"Failed to install {req_type} dependency. "
                f"Check all {req_type} dependencies:\n{formatted_reqs}"
            )
        super().__init__(f'\n{"*" * 40}\n{msg}\n{"*" * 40}\n')


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
        self._build_requirements = build_requirements
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
            + self._ctx.pip_constraint_args
            + [
                "-r",
                str(req_filename.absolute()),
            ],
            cwd=str(self.path.parent),
            network_isolation=False,
        )
        logger.info("installed dependencies into build environment in %s", self.path)


def prepare_build_environment(
    ctx: context.WorkContext,
    req: Requirement,
    sdist_root_dir: pathlib.Path,
) -> pathlib.Path:
    logger.info(f"{req.name}: preparing build environment")

    build_system_dependencies = dependencies.get_build_system_dependencies(
        ctx, req, sdist_root_dir
    )

    build_backend_dependencies = dependencies.get_build_backend_dependencies(
        ctx, req, sdist_root_dir
    )

    build_sdist_dependencies = dependencies.get_build_sdist_dependencies(
        ctx, req, sdist_root_dir
    )

    try:
        build_env = BuildEnvironment(
            ctx,
            sdist_root_dir.parent,
            build_system_dependencies
            | build_backend_dependencies
            | build_sdist_dependencies,
        )
    except subprocess.CalledProcessError as err:
        # Pip has no API, so parse its output looking for what it
        # couldn't install. If we don't find something, just re-raise
        # the exception we already have.
        logger.error(f"{req.name}: failed to create build environment for {req}: {err}")
        logger.info(f"looking for pattern in {err.output!r}")
        match = _pip_missing_dependency_pattern.search(err.output)
        if match:
            raise MissingDependency(
                ctx,
                RequirementType.BUILD,
                match.groups()[0],
                build_system_dependencies
                | build_backend_dependencies
                | build_sdist_dependencies,
            ) from err
        raise
    return build_env.path
