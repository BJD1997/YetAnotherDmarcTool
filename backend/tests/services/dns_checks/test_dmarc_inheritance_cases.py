"""Runs shared/dmarc-inheritance-cases.json against the backend's
effective_policies — the frontend Policy Builder runs the same file against
its own copy of these rules (dmarcInheritance.test.ts)."""

import json
from pathlib import Path

import pytest

from app.services.dns_checks.dmarc_record import effective_policies

_CASES = json.loads(
    (Path(__file__).resolve().parents[4] / "shared" / "dmarc-inheritance-cases.json").read_text()
)["cases"]


@pytest.mark.parametrize("case", _CASES, ids=[c["name"] for c in _CASES])
def test_shared_inheritance_case(case):
    effective = effective_policies(case["tags"])
    for tag in ("p", "sp", "np"):
        assert {"policy": effective[tag].policy, "inherited": effective[tag].inherited} == case["expected"][tag], tag
