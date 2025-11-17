from __future__ import annotations

import functools
import typing

from packaging.requirements import Requirement
from packaging.version import Version

from . import context


class HookException(ExceptionGroup):
    """Base exception for hook failures"""

    req: Requirement
    version: Version | None

    message_template: typing.ClassVar[str] = "{info} failed"

    def __new__(
        cls, req: Requirement, version: Version | None, excs: typing.Sequence[Exception]
    ) -> HookException:
        if version is not None:
            info = f"{req.name}-{version}"
        else:
            info = req.name
        message = cls.message_template.format(info=info)
        self = super().__new__(cls, message, excs)
        self.req = req
        self.version = version
        return self

    def derive(self, excs: typing.Sequence[Exception]) -> HookException:
        cls = type(self)
        return cls(self.req, self.version, excs)


class ResolverError(HookException):
    """Failed to resolve package or version"""

    message_template = "{info} failed to resolve"


class BuildSourceDistError(HookException):
    """Failed sdist build"""

    message_template = "{info} failed to build sdist"


class BuildWheelError(HookException):
    """Failed wheel build"""

    message_template = "{info} failed to build wheel"


def hook_error_wrapper(exccls: type[HookException]) -> typing.Callable:
    def error_decorator(func: typing.Callable) -> typing.Callable:
        @functools.wraps(func)
        def error_wrapper(
            *,
            ctx: context.WorkContext,
            req: Requirement,
            **kwargs: typing.Any,
        ) -> typing.Any:
            try:
                return func(ctx=ctx, req=req, **kwargs)
            except Exception as e:
                version: str | Version | None = (
                    kwargs.get("version")
                    or kwargs.get("dist_version")
                    or kwargs.get("resolved_version")
                )
                if isinstance(version, str):
                    version = Version(version)
                raise exccls(req, version, [e]) from None

        return error_wrapper

    return error_decorator
