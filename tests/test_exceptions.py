import traceback

import pytest
from packaging.requirements import Requirement
from packaging.version import Version

from fromager import context, exceptions


@exceptions.hook_error_wrapper(exceptions.BuildWheelError)
def failure(ctx: context.WorkContext, req: Requirement, version: Version) -> None:
    raise ValueError("msg")


def test_failure(tmp_context: context.WorkContext) -> None:
    req = Requirement("example")
    version = Version("1.0")
    with pytest.raises(exceptions.HookException) as excinfo:
        failure(ctx=tmp_context, req=req, version=version)
    out = "".join(traceback.format_exception(excinfo.value))
    assert "example-1.0" in out
    assert "ValueError: msg" in out
    assert 'raise ValueError("msg")' in out
