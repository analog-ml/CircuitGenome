"""Tests for multi-level SR against examples/acst_results/netlist.ckt."""
from pathlib import Path

import pytest

from circuitgenome.recognizer.netlist_parser import parse
from circuitgenome.recognizer.subcircuit_recognizer import recognize

_NETLIST_PATH = Path(__file__).parent.parent / "examples" / "acst_results" / "netlist.ckt"


@pytest.fixture(scope="module")
def parsed():
    return parse(_NETLIST_PATH.read_text())


@pytest.fixture(scope="module")
def sr(parsed):
    return recognize(parsed)


def test_parse_netlist_ckt(parsed):
    assert parsed.name == "s_2_3"
    assert len(parsed.devices) == 21
    cap_refs = {d.ref for d in parsed.devices if d.type == "capacitor"}
    assert cap_refs == {"c1", "c2"}


def test_sr_level0_all_devices_recognized(sr):
    primitive_names = {s.name for s in sr.structures if not s.children}
    assert "diode_connected_nmos" in primitive_names
    assert "nmos" in primitive_names
    assert "diode_connected_pmos" in primitive_names
    assert "pmos" in primitive_names
    assert "capacitor" in primitive_names
    assert sr.unrecognized_devices == []


def test_sr_multi_level_structure_forest(sr):
    names = {s.name for s in sr.structures}

    # Level-1 structural composites
    assert "current_mirror_nmos" in names
    assert "diode_stack_nmos" in names
    assert "cascode_pair_nmos" in names

    # Level-2 composite
    assert "cascode_current_mirror_nmos" in names

    # Children are populated for the level-2 structure
    ccm = [s for s in sr.structures if s.name == "cascode_current_mirror_nmos"]
    assert len(ccm) == 1
    assert len(ccm[0].children) == 2

    # m16 sharing: appears as the diode-connected ref in multiple current_mirror_nmos
    simple_mirrors = [s for s in sr.structures if s.name == "current_mirror_nmos"]
    assert len(simple_mirrors) >= 2
    m16_in_children = sum(
        1
        for sm in simple_mirrors
        for child in sm.children
        if any(d.ref == "m16" for d in child.devices)
    )
    assert m16_in_children >= 2
