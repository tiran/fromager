"""Fromager wheel cache helpers

Handles retrieving wheels from local and remote wheel cache servers.
"""

from __future__ import annotations

import dataclasses
import logging
import os
import pathlib
import typing

from packaging.requirements import Requirement
from packaging.utils import (
    parse_wheel_filename,
)
from packaging.version import Version

from . import resolver, wheels
from .candidate import Candidate
from .constraints import Constraints
from .requirements_file import RequirementType

if typing.TYPE_CHECKING:
    from . import context


logger = logging.getLogger(__name__)


def lookup_wheel_in_caches(
    *,
    ctx: context.WorkContext,
    req: Requirement,
    version: Version,
    req_type: RequirementType | None = None,
    cache_wheel_server_urls: tuple[str, ...] = (),
) -> tuple[Candidate, bool] | tuple[None, None]:
    """Lookup wheel in local and remote caches

    On success, returns a ``Candidate`` object and a flag whether the wheel
    is in local cache or a remote cache. If the flag is true, then the wheel
    is cached locally and ``candidate.url`` points to a local file. If the
    flag is false, then the wheel is on a remote cache server.

    If the wheel is not found in caches, returns None.

    A cached wheel has a matching version, build tag, and a compatible
    platform tag.
    """
    pbi = ctx.package_build_info(req)
    # look up in local wheel server, first
    local_cache = LocalWheelCacheProvider(
        path=ctx.wheels_downloads if not pbi.pre_built else ctx.wheels_prebuilt,
        flat=True,
        constraints=ctx.constraints,
        req_type=req_type,
    )
    candidate = local_cache.find_candidate_with_buildtag(
        ctx=ctx, req=req, version=version
    )
    if candidate is not None:
        return candidate, True

    for server_url in cache_wheel_server_urls:
        remote_cache = PyPIWheelCacheProvider(
            server_url=server_url,
            constraints=ctx.constraints,
            req_type=req_type,
        )
        candidate = remote_cache.find_candidate_with_buildtag(
            ctx=ctx, req=req, version=version
        )
        if candidate is not None:
            return candidate, False

    return None, None


def download_wheel_to_cache(
    *,
    ctx: context.WorkContext,
    candidate: Candidate,
) -> Candidate:
    """Download wheel from remote URL to internal cache"""
    if not candidate.url or "://" not in candidate.url:
        raise ValueError("Candidate url {candidate.url} is not a remote url")
    req = Requirement(candidate.name)
    wheel_filename = wheels.download_wheel(
        req=req, wheel_url=candidate.url, output_directory=ctx.wheels_downloads
    )
    return dataclasses.replace(candidate, url=wheel_filename)


class PyPIWheelCacheProvider(resolver.PyPIProvider):
    """Internal PyPI provider for wheel caches

    Specialized subclass of PyPI provider for remote wheel caches.
    """

    provider_description: typing.ClassVar[str] = (
        "PyPI wheel cache resolver (searching at {self.sdist_server_url})"
    )

    def __init__(
        self,
        *,
        server_url: str,
        constraints: Constraints | None = None,
        req_type: RequirementType | None = None,
        use_resolver_cache: bool = True,
    ):
        super().__init__(
            include_sdists=False,
            include_wheels=True,
            sdist_server_url=server_url,
            ignore_platform=False,
            constraints=constraints,
            req_type=req_type,
            use_resolver_cache=use_resolver_cache,
            cooldown=None,
            supports_upload_time=False,
        )

    def find_candidate_with_buildtag(
        self,
        context: context.WorkContext,
        req: Requirement,
        version: Version,
    ) -> Candidate | None:
        """Find a specific candidate wheel with matching build tag

        Find a wheel with exact version match and exact build tag match.
        """
        if self.include_sdists or not self.include_wheels or self.ignore_platform:
            raise ValueError("Provider must only include platform wheels")
        identifier = self.identify(req.name)
        candidates = self.find_matches(
            identifier=identifier,
            requirements={identifier: [f"{req.name}=={version}"]},
            incompatibilities={identifier: []},
        )
        if not candidates:
            return None
        pbi = context.package_build_info(req)
        build_tag = pbi.build_tag(version)
        if build_tag == (0, ""):
            build_tag = ()

        for candidate in candidates:
            if candidate.build_tag == build_tag:
                return candidate
            # (0, "") and () are equivalent
            if build_tag == () and candidate.build_tag == (0, ""):
                return candidate
        return None


class LocalWheelCacheProvider(PyPIWheelCacheProvider):
    """Lookup wheels from a local directory

    The provider looks up wheels file in a local directory or directory tree.
    A flat provider has all wheels in one directory. If flat is false, then
    the provider expects nested directories like on PyPI:

    - flat: ``{path}/meson_python-0.19.0-2-py3-none-any.whl``
    - nested: ``{path}/meson-python/meson_python-0.19.0-2-py3-none-any.whl``

    Caching is disabled. Local file look-ups are fast and we need to pick up
    any changes as soon as possible.
    """

    provider_description: typing.ClassVar[str] = (
        "Local wheel provider (path: {self.path}, flat: {self.flat})"
    )

    def __init__(
        self,
        *,
        path: pathlib.Path,
        flat: bool,
        constraints: Constraints | None = None,
        req_type: RequirementType | None = None,
    ) -> None:
        super().__init__(
            sdist_server_url="",
            constraints=constraints,
            req_type=req_type,
            use_resolver_cache=False,
        )
        self.path = path.resolve()
        self.flat = flat

    @property
    def cache_key(self) -> str:
        raise NotImplementedError()

    def find_candidates(self, identifier: str) -> resolver.Candidates:
        """Find candidates in directory

        Iterates through all versions in the VersionMap and creates Candidate
        objects with the associated URLs.
        """
        identifier = self.identify(identifier)
        # directory uses canonical name with dash
        path = self.path if self.flat else self.path / identifier
        # wheels must start with 'some_name-'
        prefix = identifier.replace("-", "_") + "-"

        candidates: list[Candidate] = []

        for entry in os.scandir(path):
            if (
                not entry.is_file()
                or not entry.name.startswith(prefix)
                or not entry.name.endswith(".whl")
            ):
                # skip entries that are not a wheel file and don't
                continue
            _, version, build_tag, tags = parse_wheel_filename(entry.name)
            if not tags.intersection(resolver.SUPPORTED_TAGS):
                # not compatible with current platform
                continue
            candidate = Candidate(
                name=identifier,
                version=version,
                url=entry.path,
                is_sdist=False,
                build_tag=build_tag,
                has_metadata=False,
                upload_time=None,
            )
            candidates.append(candidate)

        return candidates
