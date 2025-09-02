"""Version and SpecifierSet parsing with LRU cache"""

import functools
import logging
import re
import typing

from packaging.specifiers import Specifier, SpecifierSet
from packaging.tags import Tag, parse_tag
from packaging.utils import (
    BuildTag,
    InvalidSdistFilename,
    InvalidWheelFilename,
    NormalizedName,
    _build_tag_regex,
    canonicalize_name,
)
from packaging.version import InvalidVersion, Version

logger = logging.getLogger(__name__)


# (alpha|a|beta|b|preview|pre|c|rc|dev|\+)
# a: alpha, a
# b: beta, b
# c: rc
# e: preview, pre, dev
# +: local version
_no_cache = frozenset("abce+")


def cached_version(ver: str) -> Version:
    """Parse version string and return cached version"""
    if _no_cache.intersection(ver):
        # do not cache alpha, beta, rc, preview, and local version strings
        return Version(ver)
    else:
        return _cached_version(ver)


@functools.lru_cache(maxsize=1000)
def _cached_version(s: str) -> Version:
    return Version(s)


@functools.lru_cache(maxsize=128)
def cached_specifierset(
    specifiers: str | frozenset[Specifier] | tuple[Specifier] = "",
    prereleases: bool | None = None,
) -> SpecifierSet:
    """Parse SpecifierSet string and return cached version"""
    return SpecifierSet(specifiers, prereleases)


def log_cache_stats(level=logging.INFO) -> None:
    logger.log(level, "cached_version LRU cache: %r", _cached_version.cache_info())
    logger.log(
        level, "cached_specifierset LRU cache: %r", cached_specifierset.cache_info()
    )


def cached_parse_sdist_filename(filename: str) -> tuple[NormalizedName, Version]:
    """Parse sdist filename with cached version

    Copied from packaging.utils.
    """
    if filename.endswith(".tar.gz"):
        file_stem = filename[: -len(".tar.gz")]
    elif filename.endswith(".zip"):
        file_stem = filename[: -len(".zip")]
    else:
        raise InvalidSdistFilename(
            f"Invalid sdist filename (extension must be '.tar.gz' or '.zip'):"
            f" {filename!r}"
        )

    # We are requiring a PEP 440 version, which cannot contain dashes,
    # so we split on the last dash.
    name_part, sep, version_part = file_stem.rpartition("-")
    if not sep:
        raise InvalidSdistFilename(f"Invalid sdist filename: {filename!r}")

    name = canonicalize_name(name_part)

    try:
        version = cached_version(version_part)
    except InvalidVersion as e:
        raise InvalidSdistFilename(
            f"Invalid sdist filename (invalid version): {filename!r}"
        ) from e

    return name, version


_invalid_name = re.compile(r"^[\w\d._]*$", re.UNICODE)


def cached_parse_wheel_filename(
    filename: str,
) -> tuple[NormalizedName, Version, BuildTag, frozenset[Tag]]:
    """Parse wheel filename with cached version

    Copied from packaging.utils.
    """
    if not filename.endswith(".whl"):
        raise InvalidWheelFilename(
            f"Invalid wheel filename (extension must be '.whl'): {filename!r}"
        )

    filename = filename[:-4]
    dashes = filename.count("-")
    if dashes not in (4, 5):
        raise InvalidWheelFilename(
            f"Invalid wheel filename (wrong number of parts): {filename!r}"
        )

    parts = filename.split("-", dashes - 2)
    name_part = parts[0]
    # See PEP 427 for the rules on escaping the project name.
    if "__" in name_part or _invalid_name.match(name_part) is None:
        raise InvalidWheelFilename(f"Invalid project name: {filename!r}")
    name = canonicalize_name(name_part)

    try:
        version = cached_version(parts[1])
    except InvalidVersion as e:
        raise InvalidWheelFilename(
            f"Invalid wheel filename (invalid version): {filename!r}"
        ) from e

    if dashes == 5:
        build_part = parts[2]
        build_match = _build_tag_regex.match(build_part)
        if build_match is None:
            raise InvalidWheelFilename(
                f"Invalid build number: {build_part} in {filename!r}"
            )
        build = typing.cast(BuildTag, (int(build_match.group(1)), build_match.group(2)))
    else:
        build = ()
    tags = parse_tag(parts[-1])
    return (name, version, build, tags)
