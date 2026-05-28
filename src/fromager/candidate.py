from __future__ import annotations

import dataclasses
import datetime
import logging
import typing
from collections.abc import Iterable
from io import BytesIO
from zipfile import ZipFile

from packaging.metadata import Metadata
from packaging.requirements import Requirement
from packaging.utils import BuildTag, NormalizedName, canonicalize_name
from packaging.version import Version

from .request_session import session

if typing.TYPE_CHECKING:
    from .packagesettings import PackageSettings

logger = logging.getLogger(__name__)


@dataclasses.dataclass
class Cooldown:
    """Policy for rejecting recently-published package versions.

    bootstrap_time is fixed at construction so all resolutions in a single run
    share the same cutoff.
    """

    min_age: datetime.timedelta
    bootstrap_time: datetime.datetime = dataclasses.field(
        default_factory=lambda: datetime.datetime.now(datetime.UTC)
    )


class CooldownPolicy:
    """Per-package and per-version cooldown overrides on top of a global minimum age.

    When ``min_release_age`` is ``timedelta(0)`` the cooldown is globally
    disabled and ``filter_candidates`` returns all candidates unchanged.

    ``bootstrap_time`` is captured once at construction so every resolution
    within a single run shares the same reference point.
    """

    bootstrap_time: datetime.datetime = datetime.datetime.now(datetime.UTC)

    def __init__(
        self, min_release_age: datetime.timedelta = datetime.timedelta(0)
    ) -> None:
        self.min_release_age = min_release_age
        self.package_cooldowns: dict[NormalizedName, datetime.timedelta] = {}
        self.cooldown_exempt_versions: set[tuple[NormalizedName, Version]] = set()

    def set_package_cooldowns(self, packages: Iterable[PackageSettings]) -> None:
        """Populate per-package cooldowns from package settings.

        Clears existing entries first.  Only packages whose
        ``resolver_dist.min_release_age`` is set (not ``None``) are recorded.
        """
        self.package_cooldowns.clear()
        for pkg in packages:
            days = pkg.resolver_dist.min_release_age
            if days is not None:
                self.package_cooldowns[pkg.name] = datetime.timedelta(days=days)

    def add_cooldown_exempt_versions(self, requirements: Iterable[Requirement]) -> None:
        """Populate per-version cooldowns from equality-pinned requirements.

        A requirement is only recorded when ``_has_equality_pin()`` returns
        ``True`` (single exact ``==`` pin without wildcards).
        """
        for req in requirements:
            if _has_equality_pin(req):
                name = canonicalize_name(req.name)
                version = Version(next(iter(req.specifier)).version)
                self.cooldown_exempt_versions.add((name, version))

    def filter_candidates(self, candidates: Iterable[Candidate]) -> list[Candidate]:
        """Return only candidates that satisfy the cooldown policy.

        A candidate is kept when any of these conditions is true:

        * Its ``(name, version)`` pair is in ``cooldown_exempt_versions``
          (equality-pinned, cooldown bypassed).
        * The effective minimum age for its package is zero (cooldown
          disabled via per-package override).
        * Its ``upload_time`` is at least *min_age* before
          ``bootstrap_time``.

        A candidate is rejected when:

        * Its ``upload_time`` is ``None`` (age cannot be verified).
        * It was published more recently than the effective minimum age.

        When ``min_release_age`` is ``timedelta(0)`` (globally disabled),
        all candidates are returned unchanged.
        """
        if self.min_release_age == datetime.timedelta(0):
            return list(candidates)

        result: list[Candidate] = []
        for candidate in candidates:
            name = canonicalize_name(candidate.name)
            if (name, candidate.version) in self.cooldown_exempt_versions:
                result.append(candidate)
                continue

            min_age = self.package_cooldowns.get(name, self.min_release_age)
            if min_age == datetime.timedelta(0):
                result.append(candidate)
                continue

            if candidate.upload_time is None:
                continue

            if self.bootstrap_time - candidate.upload_time >= min_age:
                result.append(candidate)

        return result


def _has_equality_pin(req: Requirement) -> bool:
    """Return ``True`` if the requirement has a single exact ``==`` pin.

    Rejects wildcard pins (``==1.*``) and compound specifiers (``==1,>2``)
    which are not true exact version pins.
    """
    specs = list(req.specifier)
    return len(specs) == 1 and specs[0].operator == "==" and "*" not in specs[0].version


@dataclasses.dataclass(frozen=True, order=True, slots=True, repr=False, kw_only=True)
class Candidate:
    name: str
    version: Version
    url: str
    is_sdist: bool | None = dataclasses.field(default=None)
    extras: tuple[str, ...] = dataclasses.field(default=(), compare=False)
    build_tag: BuildTag = dataclasses.field(default=(), compare=False)
    has_metadata: bool = dataclasses.field(default=False, compare=False)
    remote_tag: str | None = dataclasses.field(default=None, compare=False)
    remote_commit: str | None = dataclasses.field(default=None, compare=False)
    upload_time: datetime.datetime | None = dataclasses.field(
        default=None, compare=False
    )

    _metadata: Metadata | None = dataclasses.field(
        default=None, init=False, compare=False
    )
    _dependencies: list[Requirement] | None = dataclasses.field(
        default=None, init=False, compare=False
    )

    def __post_init__(self) -> None:
        # force normalized name
        object.__setattr__(self, "name", canonicalize_name(self.name))

    def __repr__(self) -> str:
        if not self.extras:
            return f"<{self.name}=={self.version}>"
        return f"<{self.name}[{','.join(self.extras)}]=={self.version}>"

    @property
    def metadata_url(self) -> str | None:
        """PEP 658: metadata is available at {url}.metadata"""
        if self.has_metadata:
            return self.url + ".metadata"
        return None

    @property
    def metadata(self) -> Metadata:
        if self._metadata is None:
            if not self.has_metadata:
                raise ValueError(f"{self.url} does not have metadata")
            metadata = get_metadata_for_wheel(self.url, self.metadata_url)
            object.__setattr__(self, "_metadata", metadata)
        assert self._metadata
        return self._metadata

    def _get_dependencies(self) -> typing.Iterable[Requirement]:
        deps = self.metadata.requires_dist or []
        extras = self.extras if self.extras else [""]

        for r in deps:
            if r.marker is None:
                yield r
            else:
                for e in extras:
                    if r.marker.evaluate({"extra": e}):
                        yield r

    @property
    def dependencies(self) -> list[Requirement]:
        if self._dependencies is None:
            dependencies = list(self._get_dependencies())
            object.__setattr__(self, "_dependencies", dependencies)
        assert self._dependencies
        return self._dependencies

    @property
    def requires_python(self) -> str | None:
        spec = self.metadata.requires_python
        return str(spec) if spec is not None else None


def get_metadata_for_wheel(
    url: str, metadata_url: str | None = None, *, validate: bool = True
) -> Metadata:
    """Get metadata for a wheel, supporting PEP 658 metadata endpoints.

    Args:
        url: URL of the wheel file
        metadata_url: Optional URL of the metadata file (PEP 658)
        validate: Whether to validate metadata (default: True)

    Returns:
        Parsed metadata as a Metadata object
    """
    # Try PEP 658 metadata endpoint first if available
    if metadata_url:
        try:
            logger.debug(
                f"Attempting to fetch metadata from PEP 658 endpoint: {metadata_url}"
            )
            response = session.get(metadata_url)
            response.raise_for_status()

            # Parse metadata directly using packaging.metadata.Metadata
            # (avoiding circular import with dependencies module)
            metadata = Metadata.from_email(response.content, validate=validate)
            logger.debug(f"Successfully retrieved metadata via PEP 658 for {url}")
            return metadata

        except Exception as e:
            logger.debug(f"Failed to fetch PEP 658 metadata from {metadata_url}: {e}")
            logger.debug(
                "Falling back to downloading full wheel for metadata extraction"
            )

    # Fallback to existing method: download wheel and extract metadata
    logger.debug(f"Downloading full wheel to extract metadata: {url}")
    data = session.get(url).content
    with ZipFile(BytesIO(data)) as z:
        for n in z.namelist():
            if n.endswith(".dist-info/METADATA"):
                metadata_content = z.read(n)
                # Parse metadata directly using packaging.metadata.Metadata
                # (avoiding circular import with dependencies module)
                return Metadata.from_email(metadata_content, validate=validate)

    # If we didn't find the metadata, raise an error
    raise ValueError(f"Could not find METADATA file in wheel: {url}")
