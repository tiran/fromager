import functools
import typing

from packaging.markers import Marker
from packaging.requirements import Requirement
from packaging.specifiers import SpecifierSet
from packaging.utils import NormalizedName, canonicalize_name
from packaging.version import Version


# TODO: remove subclassing of Requirement
# ResolvedRequirement is a subclass gradual migration of code.
@functools.total_ordering
class ResolvedRequirement(Requirement):
    """A requirement that has been resolved to a version"""

    def __init__(self, req: Requirement | str, version: Version | str) -> None:
        if not isinstance(req, Requirement):
            req = Requirement(req)
        if not isinstance(version, Version):
            version = Version(version)
        self._req: Requirement = req
        self._version: Version = version
        self._canonical_name = canonicalize_name(req.name)

    @property
    def req(self) -> Requirement:
        return self._req

    @property
    def version(self) -> Version:
        return self._version

    @property
    def canonical_name(self) -> NormalizedName:
        return self._canonical_name

    @property
    def override_name(self) -> str:
        return self._canonical_name.replace("-", "_")

    @property
    def name(self) -> str:
        return self._req.name

    @name.setter
    def name(self, _: str) -> None:
        # TODO: remove workaround for mypy error
        # "cannot override writeable attribute with read-only property"
        raise TypeError("name is immutable")

    @property
    def url(self) -> str | None:
        return self._req.url

    @url.setter
    def url(self, _: str) -> None:
        raise TypeError("url is immutable")

    @property
    def extras(self) -> set[str]:
        return self._req.extras

    @extras.setter
    def extras(self, _: str) -> None:
        raise TypeError("extras is immutable")

    @property
    def specifiers(self) -> SpecifierSet:
        return self._req.specifier

    @property
    def marker(self) -> Marker | None:
        return self._req.marker

    @marker.setter
    def marker(self, _: str) -> None:
        raise TypeError("marker is immutable")

    def __str__(self) -> str:
        # pinned, exact version
        return f"{self.canonical_name}=={self.version}"

    def __repr__(self) -> str:
        return f"<{self.__class__.__name__}('{self.req}', '{self.version}')>"

    def __fspath__(self) -> str:
        # for pathlib.Path() / ResolvedRequirement
        return f"{self.override_name}-{self.version}"

    def __hash__(self) -> int:
        return hash((self.__class__.__name__, self.req, self.version))

    def __eq__(self, other: typing.Any) -> bool:
        if not isinstance(other, ResolvedRequirement):
            return NotImplemented

        return self.req == other.req and self.version == other.version

    def __lt__(self, other: typing.Any) -> bool:
        if not isinstance(other, ResolvedRequirement):
            return NotImplemented
        # sort by canonical name and resolved version
        return (
            self.canonical_name,
            self.version,
        ) < (
            other.canonical_name,
            other.version,
        )
