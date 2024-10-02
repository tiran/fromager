import os

from packaging.requirements import Requirement
from packaging.utils import canonicalize_name
from packaging.version import Version

from fromager.requirements import ResolvedRequirement

REQ = Requirement("test-req>1.0.0")
VER = Version("2.0.0")

def test_resolvedrequirement():
    rr = ResolvedRequirement(REQ, VER)
    assert rr.req is REQ
    assert rr.version is VER
    assert rr.name == REQ.name
    assert rr.canonical_name == canonicalize_name(REQ.name)
    assert rr.override_name == "test_req"
    assert rr.url is None
    assert rr.specifiers == REQ.specifier
    assert rr.marker == REQ.marker
    assert str(rr) == "test-req==2.0.0"
    assert repr(rr) == "<ResolvedRequirement('test-req>1.0.0', '2.0.0')>"
    assert os.fspath(rr) == "test_req-2.0.0"
    assert hash(rr)
    assert rr == rr
    assert not rr < rr
    assert isinstance(rr, ResolvedRequirement)
    assert isinstance(rr, Requirement)

    assert rr == ResolvedRequirement(str(REQ), str(VER))
