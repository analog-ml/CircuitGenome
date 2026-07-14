"""Tests for the GF180MCU PDK integration (corner-library + subckt gm/Id tech).

The GF180 tech selects devices via a corner ``.lib`` and instantiates PDK
subcircuits (``nmos_3p3``/``pmos_3p3``), unlike PTM's flat ``.include`` of
``.model nmos``/``pmos`` cards.  These tests cover the schema, the ngspice
deck-generation differences, and that sizing dispatches to the gm/Id path
(none require ngspice — they exercise the pure machinery).
"""
from pathlib import Path

import pytest

from circuitgenome.recognizer import assign_slots, parse, recognize
from circuitgenome.sizer.shared import spice_sim
from circuitgenome.sizer.shared.spice import deck, measure
from circuitgenome.sizer.shared.device_model import build_device_model
from circuitgenome.sizer.shared.loader import load_tech
from circuitgenome.sizer.shared.models import SizingSpec
from circuitgenome.sizer.sizer import size_circuit
from circuitgenome.synthesizer.loader import load_topologies

_CKT_DIR = (Path(__file__).resolve().parent.parent / "circuits"
            / "two_stage_opamp_single_ended")
_TOPO = "two_stage_opamp_single_ended"


def test_gf180_tech_loads_lib_and_device_map():
    tech = load_tech("gf180mcu")
    assert tech.spice_model is None and tech.spice_lib is not None
    assert Path(tech.spice_lib.file).exists()
    assert Path(tech.spice_lib.design).exists()
    assert tech.spice_lib.corner == "typical"
    assert set(tech.spice_lib.corners) == {"typical", "ss", "ff", "sf", "fs"}
    assert tech.device_map == {"nmos": "nmos_3p3", "pmos": "pmos_3p3"}
    assert tech.gmid_lut is not None
    # A LUT-bearing tech dispatches to the gm/Id device model.
    assert build_device_model(tech).is_gmid


def test_gf180_emit_model_selects_corner():
    tech = load_tech("gf180mcu")
    nominal = deck._emit_model(tech)
    assert ".include" in nominal and "design.ngspice" in nominal
    assert '.lib "' in nominal and "sm141064.ngspice" in nominal
    assert nominal.rstrip().endswith("typical")
    assert deck._emit_model(tech, "ff").rstrip().endswith("ff")
    # PTM stays a flat .include (no .lib).
    assert ".lib" not in deck._emit_model(load_tech("ptm45"))


def test_gf180_device_translation_m_to_x():
    tech = load_tech("gf180mcu")
    body = ["m1_input_pair net1 in1 net2 net2 pmos W=10.0u L=0.56u",
            "c1_compensation net2 out 1p"]
    out = deck._emit_body(tech, body)
    assert out[0] == "x1_input_pair net1 in1 net2 net2 pmos_3p3 w=10.0u l=0.56u"
    assert out[1] == "c1_compensation net2 out 1p"  # non-MOS untouched
    # PTM (no device_map) leaves the M-device line unchanged.
    assert deck._emit_body(load_tech("ptm45"), body) == body


def test_gf180_op_handle_is_nested():
    tech = load_tech("gf180mcu")
    assert deck._dev_prefix(tech, "m1_input_pair", "pmos") == "@m.xdut.x1_input_pair.m0"
    assert deck._dev_prefix(load_tech("ptm45"), "m1_input_pair", "pmos") == "@m.xdut.m1_input_pair"


def test_device_handle_and_um_units():
    """Per-polarity OP handles + micron W/L for a wl_units="um" PDK (sky130)."""
    import dataclasses
    tech = dataclasses.replace(
        load_tech("gf180mcu"),
        device_map={"nmos": "sky130_fd_pr__nfet_01v8", "pmos": "sky130_fd_pr__pfet_01v8"},
        device_handle={"nmos": "msky130_fd_pr__nfet_01v8", "pmos": "msky130_fd_pr__pfet_01v8"},
        wl_units="um",
    )
    assert (deck._dev_prefix(tech, "m1_input_pair", "pmos")
            == "@m.xdut.x1_input_pair.msky130_fd_pr__pfet_01v8")
    assert (deck._dev_prefix(tech, "m5_tail", "nmos")
            == "@m.xdut.x5_tail.msky130_fd_pr__nfet_01v8")
    # µm units: the SI micro suffix is dropped (bare numbers = microns under
    # the library's `.option scale=1.0u`); non-MOS lines untouched.
    body = ["m1_input_pair net1 in1 net2 net2 pmos W=10.00000u L=0.56000u",
            "c1_compensation net2 out 1p"]
    out = deck._emit_body(tech, body)
    assert out[0] == ("x1_input_pair net1 in1 net2 net2 "
                      "sky130_fd_pr__pfet_01v8 w=10.00000 l=0.56000")
    assert out[1] == "c1_compensation net2 out 1p"


def _first_feasible_size():
    """Size a couple of active-load circuits at GF180; return the gm/Id result."""
    spec = SizingSpec(vdd=3.3, vss=0.0, ibias=40e-6, cl=2e-12,
                      second_stage_current_ratio=2.0, gain_min_db=45,
                      gbw_min_hz=8e5, phase_margin_min_deg=60, slew_rate_min_vps=2e5)
    topo = next(t for t in load_topologies() if t.name == _TOPO)
    tech = load_tech("gf180mcu")
    for name in ("circuit_0082_flat.ckt", "circuit_0090_flat.ckt"):
        ckt = _CKT_DIR / name
        if not ckt.exists():
            continue
        parsed = parse(ckt.read_text())
        fbr = assign_slots(recognize(parsed), topo)
        return size_circuit(parsed, recognize(parsed), fbr, topo, tech, spec)
    return None


def test_gf180_sizes_via_gmid_path():
    """GF180 sizing runs the gm/Id pipeline (pure — no ngspice) and picks L on grid."""
    r = _first_feasible_size()
    if r is None:
        pytest.skip("gf180 active-load fixtures not present")
    assert r.solver_status == "GMID"
    assert r.transistors
    # Lengths land on the GF180 grid (Lmin 0.28 µm).
    assert all(s.l_um >= 0.28 - 1e-9 for s in r.transistors.values())
