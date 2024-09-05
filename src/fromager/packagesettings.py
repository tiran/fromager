import logging
import os
import pathlib
import string
import types
import typing
from collections.abc import Mapping

import pydantic
import yaml
from packaging.utils import BuildTag, NormalizedName, canonicalize_name
from packaging.version import Version
from pydantic import Field
from pydantic_core import CoreSchema, core_schema

from . import overrides

logger = logging.getLogger(__name__)


# build directory
def _before_builddirectory(p: str) -> pathlib.Path:
    result = pathlib.Path(p)
    if result.is_absolute():
        raise ValueError(f"{result!r} is not a relative path")
    return result


BuildDirectory = typing.Annotated[
    pathlib.Path,
    pydantic.BeforeValidator(_before_builddirectory),
]


# version
class PackageVersion(Version):
    """Pydantic-aware package version"""

    @classmethod
    def validate(cls, v: typing.Any, info: core_schema.ValidationInfo) -> Version:
        if isinstance(v, Version):
            return v
        return Version(v)

    @classmethod
    def __get_pydantic_core_schema__(
        cls, source_type: typing.Any, handler: pydantic.GetCoreSchemaHandler
    ) -> CoreSchema:
        return core_schema.with_info_plain_validator_function(
            cls.validate,
            serialization=core_schema.plain_serializer_function_ser_schema(
                str, when_used="json"
            ),
        )


# environment variables map
def _validate_envkey(v: typing.Any) -> str:
    """Validate env key, converts int, float, bool"""
    if isinstance(v, bool):
        return "1" if v else "0"
    elif isinstance(v, int | float):
        return str(v)
    elif not isinstance(v, str):
        raise TypeError(f"unsupported type {type(v)}: {v!r}")
    if "$(" in v:
        raise ValueError(f"'{v}': subshell '$()' is not supported.")
    return v.strip()


EnvKey = typing.Annotated[
    str,
    pydantic.BeforeValidator(_validate_envkey),
]

EnvVars = dict[str, EnvKey]

# Package validates and transforms name to canonicalized name
Package = typing.Annotated[
    NormalizedName,
    pydantic.BeforeValidator(lambda pkg: canonicalize_name(pkg, validate=True)),
]

# patch mapping
PatchMap = dict[Version, typing.Iterable[pathlib.Path]]

# URL or filename with templating
Template = typing.NewType("Template", str)

# build variant
Variant = typing.NewType("Variant", str)

# Changelog
GlobalChangelog = Mapping[Variant, list[str]]
VariantChangelog = Mapping[PackageVersion, list[str]]

# common settings
MODEL_CONFIG = pydantic.ConfigDict(
    # don't accept unknown keys
    extra="forbid",
    # all fields are immutable
    frozen=True,
    # read inline doc strings
    use_attribute_docstrings=True,
)


class ResolveSource(pydantic.BaseModel):
    """Packages resolver dist"""

    model_config = MODEL_CONFIG

    sdist_server_url: str | None = None
    """Source distribution download server (default: PyPI)"""

    include_sdists: bool = True
    """Use sdists to resolve? (default: yes)"""

    include_wheels: bool = False
    """Use wheels to resolve? (default: no)"""


class DownloadSource(pydantic.BaseModel):
    """Package download source

    Download package sources from an alternative source, e.g. GitHub release.
    """

    model_config = MODEL_CONFIG

    url: Template | None = None
    """Source download url (string template)"""

    destination_filename: Template | None = None
    """Rename file (filename without path)"""

    @pydantic.field_validator("destination_filename")
    @classmethod
    def validate_destination_filename(cls, v):
        if os.pathsep in v:
            raise ValueError(f"must not contain {os.pathsep}")
        return v


class VariantInfo(pydantic.BaseModel):
    """Variant information for a package"""

    model_config = MODEL_CONFIG

    env: EnvVars = Field(default_factory=dict)
    """Additional env vars (overrides package env vars)"""

    wheel_server_url: str | None = None
    """Alternative package index for pre-built wheel"""

    pre_built: bool = False
    """Use pre-built wheel from index server?"""


_DictStrAny = dict[str, typing.Any]


class PackageSettings(pydantic.BaseModel):
    """Package settings

    yaml::
        build_dir: python
        changelog:
            "1.0.1":
                - fixed bug
        env:
            EGG: spam
        download_source:
            url: https://egg.test
            destination_filename: new_filename
        resolve_source:
            sdist_server_url: https://sdist.test/egg
            include_sdists: true
            include_wheels: false
        variants:
            cpu:
                env:
                    EGG: spamalot
                wheel_server_url: https://wheel.test/simple
            rocm:
                pre_built: True
    """

    model_config = MODEL_CONFIG

    name: Package
    """Canonicalized package name"""

    has_config: bool
    """package has override setting"""

    build_dir: BuildDirectory | None = None
    """sub-directory with setup.py or pyproject.toml"""

    changelog: VariantChangelog = Field(default_factory=dict)
    """Changelog entries"""

    env: EnvVars = Field(default_factory=dict)
    """Common env var for all variants"""

    download_source: DownloadSource = Field(default_factory=DownloadSource)
    """Alternative source download settings"""

    resolve_source: ResolveSource = Field(default_factory=ResolveSource)
    """Resolve distribution version"""

    variants: Mapping[Variant, VariantInfo] = Field(default_factory=dict)
    """Variant configuration"""

    @pydantic.field_validator(
        "download_source", "resolve_source", "variants", mode="before"
    )
    @classmethod
    def before_none_dict(
        cls, v: _DictStrAny | None, info: core_schema.ValidationInfo
    ) -> _DictStrAny:
        if v is None:
            v = {}
        return v

    @classmethod
    def from_mapping(
        cls,
        package: str | Package,
        parsed: dict[str, typing.Any],
        *,
        source: pathlib.Path | str | None,
        has_config: bool,
    ) -> "PackageSettings":
        """Load from a dict"""
        package = Package(canonicalize_name(package, validate=True))
        try:
            return cls(name=package, has_config=has_config, **parsed)
        except Exception as err:
            raise RuntimeError(
                f"{package}: failed to load settings (source: {source!r}): {err}"
            ) from err

    @classmethod
    def from_string(
        cls,
        package: str | Package,
        raw_yaml: str,
        *,
        source: pathlib.Path | str | None = None,
    ) -> "PackageSettings":
        """Load from raw yaml string"""
        parsed: typing.Any = yaml.safe_load(raw_yaml)
        if parsed is None:
            # empty file
            parsed = {}
        elif not isinstance(parsed, Mapping):
            raise TypeError(
                f"{package}: invalid yaml, not a dict (source: {source!r}): {parsed}"
            )
        return cls.from_mapping(package, parsed, source=source, has_config=True)

    @classmethod
    def from_file(cls, filename: pathlib.Path) -> "PackageSettings":
        """Load from file

        Raises :exc:`FileNotFound` when the file is not found.
        The package name is taken from the stem of the file name.
        """
        filename = filename.absolute()
        logger.debug("Loading package config from %s", filename)
        raw_yaml = filename.read_text(encoding="utf-8")
        return cls.from_string(filename.stem, raw_yaml, source=filename)

    @classmethod
    def from_default(cls, package: str | Package) -> "PackageSettings":
        """Create a default package setting"""
        return cls.from_mapping(package, {}, source="default", has_config=False)

    @property
    def override_module_name(self) -> str:
        """Override module name from package name"""
        return self.name.replace("-", "_")

    def serialize(
        self,
        mode: str = "python",
        exclude_defaults=True,
        exclude_unset=True,
        exclude=frozenset({"name", "has_config"}),
        **kwargs,
    ) -> dict[str, typing.Any]:
        """Serialize package configuration"""
        return self.model_dump(
            mode=mode,
            # exclude defaults
            exclude_defaults=exclude_defaults,
            exclude_unset=exclude_unset,
            # name and has_config are not serialized
            exclude=exclude,
            **kwargs,
        )


def _resolve_template(
    template: Template,
    pkg: Package,
    version: Version | None = None,
) -> str:
    template_env: dict[str, str] = {"canonicalized_name": str(pkg)}
    if version:
        template_env["version"] = str(version)

    try:
        return string.Template(template).substitute(template_env)
    except KeyError:
        logger.warning(
            f"{pkg}: Couldn't resolve url or name for {template} using the template: {template_env}"
        )
        raise


class PackageBuildInfo:
    """Package build information

    Public API for PackageSettings with i
    """

    def __init__(self, ctx: "Settings", ps: PackageSettings) -> None:
        self._variant = typing.cast(Variant, ctx.variant)
        self._patches_dir = ctx.patches_dir
        self._variant_changelog = ctx.variant_changelog()
        self._ps = ps
        self._plugin_module: types.ModuleType | None | typing.Literal[False] = False
        self._patches: PatchMap | None = None

    @property
    def package(self) -> NormalizedName:
        """Package name"""
        return typing.cast(NormalizedName, self._ps.name)

    @property
    def variant(self) -> Variant:
        """Variant name"""
        return self._variant

    @property
    def plugin(self) -> types.ModuleType | None:
        """Get Fromager plugin module"""
        if self._plugin_module is False:
            exts = overrides._get_extensions()
            try:
                mod = exts[self.override_module_name].plugin
                self._plugin_module = typing.cast(types.ModuleType, mod)
            except KeyError:
                self._plugin_module = None
        return self._plugin_module

    def get_patches(self) -> PatchMap:
        """Get a mapping of version to list of patches"""
        if self._patches is None:
            patches: PatchMap = {}
            pattern = f"{self.override_module_name}-*"
            prefix_len = len(pattern) - 1
            for patchdir in self._patches_dir.glob(pattern):
                version = Version(patchdir.name[prefix_len:])
                patches[version] = sorted(patchdir.glob("*.patch"))
            self._patches = patches
        return self._patches

    @property
    def has_config(self) -> bool:
        """Does the package have a config file?"""
        return self._ps.has_config

    @property
    def pre_built(self) -> bool:
        """Does the variant use pre-build wheels?"""
        vi = self._ps.variants.get(self.variant)
        if vi is not None:
            return vi.pre_built
        return False

    @property
    def wheel_server_url(self) -> str | None:
        """Alternative package index for pre-build wheel"""
        vi = self._ps.variants.get(self.variant)
        if vi is not None and vi.wheel_server_url is not None:
            return str(vi.wheel_server_url)
        return None

    @property
    def override_module_name(self) -> str:
        """Override module name from package name"""
        return self._ps.override_module_name

    def download_source_url(
        self,
        version: Version | str | None = None,
        default: str | None = None,
        *,
        resolve_template: bool = True,
    ) -> str | None:
        """sdist download URL"""
        if version is not None and isinstance(version, str):
            version = Version(version)
        template = self._ps.download_source.url
        if template is None and default:
            template = typing.cast(Template, default)
        if template and resolve_template:
            return _resolve_template(template, self.package, version)
        elif template:
            return str(template)
        else:
            return None

    def download_source_destination_filename(
        self,
        version: Version | str | None = None,
        default: str | None = None,
        *,
        resolve_template: bool = True,
    ) -> str | None:
        """Rename sdist to dest filename"""
        if version is not None and isinstance(version, str):
            version = Version(version)
        template = self._ps.download_source.destination_filename
        if template is None and default:
            template = typing.cast(Template, default)
        if template and resolve_template:
            return _resolve_template(template, self.package, version)
        elif template:
            return str(template)
        else:
            return None

    def resolve_source_sdist_server_url(self, default: str) -> str:
        """Package index server URL for resolving package versions"""
        url = self._ps.resolve_source.sdist_server_url
        if url is None:
            url = default
        return url

    @property
    def resolve_source_include_wheels(self) -> bool:
        """Include wheels when resolving package versions?"""
        return self._ps.resolve_source.include_wheels

    @property
    def resolve_source_include_sdists(self) -> bool:
        """Include sdists when resolving package versions?"""
        return self._ps.resolve_source.include_sdists

    def build_dir(self, sdist_root_dir: pathlib.Path) -> pathlib.Path:
        """Build directory for package (e.g. subdirectory)"""
        build_dir = self._ps.build_dir
        if build_dir is not None:
            # ensure that absolute build_dir path from settings is converted to a relative path
            relative_build_dir = build_dir.relative_to(build_dir.anchor)
            return sdist_root_dir / relative_build_dir
        return sdist_root_dir

    def build_tag(self, version: Version) -> BuildTag:
        """Build tag for version's changelog and this variant"""
        pv = typing.cast(PackageVersion, version)
        release = len(self._ps.changelog.get(pv, []))
        release += len(self._variant_changelog)
        if release == 0:
            return ()
        # suffix = "." + self.variant.replace("-", "_")
        suffix = ""
        return release, suffix

    def get_extra_environ(
        self, *, template_env: dict[str, str] | None = None
    ) -> dict[str, str]:
        """Get extra environment variables for a variant

        `template_env` defaults to `os.environ`.
        """
        if template_env is None:
            template_env = os.environ.copy()
        else:
            template_env = template_env.copy()
        # chain entries so variant entries can reference general entries
        entries = list(self._ps.env.items())
        vi = self._ps.variants.get(self.variant)
        if vi is not None:
            entries.extend(vi.env.items())

        extra_environ: dict[str, str] = {}
        for key, value in entries:
            value = string.Template(value).substitute(template_env)
            extra_environ[key] = value
            # subsequent key-value pairs can depend on previously vars.
            template_env[key] = value

        return extra_environ

    def serialize(self, **kwargs) -> dict[str, typing.Any]:
        return self._ps.serialize(**kwargs)


class SettingsFile(pydantic.BaseModel):
    """Models global settings file `settings.yaml`"""

    model_config = MODEL_CONFIG

    changelog: GlobalChangelog = Field(default_factory=dict)
    """Changelog entries"""

    @classmethod
    def from_string(
        cls,
        raw_yaml: str,
        *,
        source: pathlib.Path | str | None = None,
    ) -> "SettingsFile":
        """Load from raw yaml string"""
        parsed: typing.Any = yaml.safe_load(raw_yaml)
        if parsed is None:
            # empty file
            parsed = {}
        elif not isinstance(parsed, Mapping):
            raise TypeError(f"invalid yaml, not a dict (source: {source!r}): {parsed}")
        # ignore legacy settings
        parsed.pop("pre_built", None)
        parsed.pop("packages", None)
        try:
            return cls(**parsed)
        except Exception as err:
            raise RuntimeError(
                f"failed to load global settings (source: {source!r}): {err}"
            ) from err

    @classmethod
    def from_file(cls, filename: pathlib.Path) -> "SettingsFile":
        """Load from file

        Raises :exc:`FileNotFound` when the file is not found.
        The package name is taken from the stem of the file name.
        """
        filename = filename.absolute()
        logger.info("loading settings from %s", filename)
        raw_yaml = filename.read_text(encoding="utf-8")
        return cls.from_string(raw_yaml, source=filename)


class Settings:
    """Settings interface for settings file and package settings"""

    def __init__(
        self,
        *,
        settings: SettingsFile,
        package_settings: typing.Iterable[PackageSettings],
        variant: Variant | str,
        patches_dir: pathlib.Path,
    ) -> None:
        self._settings = settings
        self._package_settings: dict[Package, PackageSettings] = {
            p.name: p for p in package_settings
        }
        self._variant = typing.cast(Variant, variant)
        self._patches_dir = patches_dir
        self._pbi_cache: dict[Package, PackageBuildInfo] = {}

    @classmethod
    def from_files(
        cls,
        *,
        settings_file: pathlib.Path,
        settings_dir: pathlib.Path,
        variant: Variant | str,
        patches_dir: pathlib.Path,
    ) -> "Settings":
        """Create Settings from settings.yaml and directory"""
        if settings_file.is_file():
            settings = SettingsFile.from_file(settings_file)
        else:
            logger.debug(
                "settings file %s does not exist, ignoring", settings_file.absolute()
            )
            settings = SettingsFile()
        package_settings = [
            PackageSettings.from_file(package_file)
            for package_file in sorted(settings_dir.glob("*.yaml"))
        ]
        return cls(
            settings=settings,
            package_settings=package_settings,
            variant=variant,
            patches_dir=patches_dir,
        )

    @property
    def variant(self) -> Variant:
        """Get current variant"""
        return self._variant

    @variant.setter
    def variant(self, v: Variant) -> None:
        """Change current variant (for testing)"""
        # reset cache
        self._pbi_cache.clear()
        self._variant = v

    @property
    def patches_dir(self) -> pathlib.Path:
        """Get directory with patches"""
        return self._patches_dir

    @patches_dir.setter
    def patches_dir(self, path: pathlib.Path) -> None:
        """Change patches_dr (for testing)"""
        self._pbi_cache.clear()
        self._patches_dir = path

    def variant_changelog(self) -> list[str]:
        """Get global changelog for current variant"""
        return list(self._settings.changelog.get(self.variant, []))

    def package_setting(self, package: str | Package) -> PackageSettings:
        """Get package settings for package"""
        package = Package(canonicalize_name(package, validate=True))
        ps = self._package_settings.get(package)
        if ps is None:
            # create and cache default settings
            ps = PackageSettings.from_default(package)
            self._package_settings[package] = ps
        return ps

    def package_build_info(self, package: str | Package) -> PackageBuildInfo:
        """Get (cached) PackageBuildInfo for package and current variant"""
        package = Package(canonicalize_name(package, validate=True))
        pbi = self._pbi_cache.get(package)
        if pbi is None:
            ps = self.package_setting(package)
            pbi = PackageBuildInfo(self, ps)
            self._pbi_cache[package] = pbi
        return pbi

    def list_pre_built(self) -> set[Package]:
        """List packages marked as pre-built"""
        return set(
            name
            for name in self._package_settings
            if self.package_build_info(name).pre_built
        )

    def list_overrides(self) -> set[Package]:
        """List packages with overrides

        - `settings/package.yaml`
        - override plugin
        - `patches/package-version/*.patch`
        """
        packages: set[Package] = set()

        # package settings with a config file
        packages.update(
            ps.name for ps in self._package_settings.values() if ps.has_config
        )

        # override plugins
        exts = overrides._get_extensions()
        packages.update(
            Package(canonicalize_name(name, validate=True)) for name in exts.names()
        )

        # patches
        for patchfile in self.patches_dir.glob("*/*.patch"):
            # parent directory has format "package-version"
            name = patchfile.parent.name.rsplit("-", 1)[0]
            packages.add(Package(canonicalize_name(name, validate=True)))

        return packages
