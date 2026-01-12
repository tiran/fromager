import abc
import csv
import email.message
import email.parser
import email.policy
import functools
import pathlib
import typing
from dataclasses import Field

from packaging.metadata import Metadata
from packaging.requirements import Requirement
from packaging.tags import Tag
from packaging.utils import BuildTag, parse_wheel_filename
from packaging.version import Version
from pydantic.dataclasses import dataclass

if typing.TYPE_CHECKING:
    import elfdeps

    from .. import build_environment, context


@dataclass(kw_only=True)
class WheelValidatorContext:
    """Context object for wheel validators"""

    ctx: context.WorkContext
    req: Requirement
    version: Version
    extra_environ: dict[str, str]
    # sdist_root_dir: pathlib.Path
    wheel_root_dir: pathlib.Path
    wheel_file: pathlib.Path
    elfinfos: list[elfdeps.ELFInfo] | None
    build_env: build_environment.BuildEnvironment
    new_build_tag: BuildTag

    @functools.cached_property
    def dist_info_dir(self) -> pathlib.Path:
        """Get absolute path to .dist-info directory"""
        # dist-info directory name is based on wheel file name. The
        # distribution name is *not* normalized.
        distribution = self.wheel_file.name.split("-", 1)[0]
        return self.wheel_root_dir / f"{distribution}-{self.version}"

    def parse_wheel_metadata(self) -> email.message.Message[str, str]:
        """Parse dist-info/WHEEL"""
        with self.dist_info_dir.joinpath("WHEEL").open(encoding="utf-8") as f:
            msg = email.parser.Parser(policy=email.policy.compat32).parsestr(f.read())
        return msg

    @functools.cached_property
    def wheel_tags(self) -> frozenset[Tag]:
        """Get wheel tags

        Wheel tags are encoded in wheel file name and dist-info/WHEEL file.
        """
        return parse_wheel_filename(self.wheel_file.name)[3]

    @functools.cached_property
    def is_purelib(self) -> bool:
        """Is the wheel pure Python (any platform)?"""
        return all(tag.platform == "any" for tag in self.wheel_tags)

    def parse_metadata(self, *, validate: bool = True) -> Metadata:
        """Read and parse METADATA file"""
        with self.dist_info_dir.joinpath("METADATA").open(mode="rb") as f:
            return Metadata.from_email(f.read(), validate=validate)

    def list_files(self, pattern: str | None = None) -> typing.Iterable[pathlib.Path]:
        """Read file list from dist-info/RECORD"""
        with self.dist_info_dir.joinpath("RECORD").open(encoding="utf-8") as f:
            records = csv.reader(f)
        for filename, _digest, _size in records:
            filepath = self.wheel_root_dir / filename
            if pattern is None or filepath.match(pattern):
                yield filepath

    @functools.cached_property
    def elf_requires(self) -> frozenset[elfdeps.SOInfo]:
        """Get required ELF libraries as SOInfo objects"""
        if self.elfinfos is None:
            return frozenset()
        requires: set[elfdeps.SOInfo] = set()
        for info in self.elfinfos:
            requires.update(info.requires)
        return frozenset(requires)

    def get_required_libraries(self, *, strip_version: bool = False) -> frozenset[str]:
        """Get required ELF libraries

        *strip_version* strips off the version suffix, ``libfoo.so.3`` ->
        ``libfoo.so``.
        """
        result: set[set] = set()
        for soinfo in self.elf_requires:
            soname = soinfo.soname
            if strip_version:
                # libfoo.so.3 -> libfoo.so
                pos = soname.rfind(".so")
                if pos > 0:
                    soname = soname[: pos + 3]
            result.add(soinfo)
        return frozenset(result)


@dataclass(kw_only=True)
class WheelValidatorBase(metaclass=abc.ABCMeta):
    """Base class for Fromager wheel validators"""

    id: str = Field(doc="Identifier (default: plugin name)")
    enabled: bool = Field(default=True, compare=False, doc="Is the plugin enabled?")
    description: str | None = Field(default=None, compare=False)
    error: typing.Literal["fatal"] | typing.Literal["warning"] = Field(
        default="fatal",
        compare=False,
        doc="Is a validation failure a warning or fatal error?",
    )

    def __call__(self, vctx: WheelValidatorContext) -> None:
        self.validate(vctx=vctx)

    @abc.abstractmethod
    def validate(self, vctx: WheelValidatorContext) -> None:
        """Validation hook"""
