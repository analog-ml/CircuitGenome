"""Tests for the gm/Id DC operating-point / cascode-aware headroom check."""
from pathlib import Path

import pytest

from circuitgenome.recognizer import assign_slots, parse, recognize
from circuitgenome.sizer.shared.loader import load_tech
from circuitgenome.sizer.shared.models import SizingSpec
from circuitgenome.sizer.sizer import size_circuit
from circuitgenome.synthesizer.loader import load_modules, load_topologies
from circuitgenome.synthesizer.netlist import to_flat_spice
from circuitgenome.synthesizer.synthesizer import enumerate_circuits

_CKT_DIR = (Path(__file__).resolve().parent.parent / "circuits"
            / "two_stage_opamp_single_ended")
_TOPO = "two_stage_opamp_single_ended"


def _spec():
    return SizingSpec(vdd=1.0, vss=0.0, ibias=15e-6, cl=2e-12,
                      second_stage_current_ratio=2.5, gain_min_db=55,
                      gbw_min_hz=1e6, phase_margin_min_deg=60, slew_rate_min_vps=0.65e6,
                      output_swing_max_v=0.8, output_swing_min_v=0.2)


def _size(netlist):
    parsed = parse(netlist)
    topo = next(t for t in load_topologies() if t.name == _TOPO)
    fbr = assign_slots(recognize(parsed), topo)
    return size_circuit(parsed, recognize(parsed), fbr, topo, load_tech("ptm45"), _spec())


def test_cascode_tail_flagged_not_feasible():
    """circuit_0110's cascode tail can't bias at 1.0 V → bias_feasible=False."""
    ckt = _CKT_DIR / "circuit_0110_flat.ckt"
    if not ckt.exists():
        pytest.skip("circuit_0110 fixture not present")
    r = _size(ckt.read_text())
    assert r.solver_status == "GMID"
    assert r.bias_feasible is False
    assert any("cascode tail" in w for w in r.warnings)


def test_simple_mirror_tail_no_cascode_warning():
    """A simple current-mirror tail is not flagged as a *cascode* collapse.

    (At ptm45 / 1.0 V mid-rail the simple tail is still headroom-tight — the #74
    advisory — but it must not get the cascode-specific warning.)
    """
    mods = load_modules()
    topo = next(t for t in load_topologies() if t.name == _TOPO)
    want = {"input_pair": "differential_pair_pmos", "load": "active_load_nmos",
            "tail_current": "current_mirror_tail_pmos", "second_stage": "common_source",
            "bias_gen": "diode_connected_mosfet_bias", "compensation": "miller_cap"}
    circ = next(c for c in enumerate_circuits(topo, mods)
                if all(c.variant_map.get(k).name == v for k, v in want.items()))
    r = _size(to_flat_spice(circ))
    assert r.solver_status == "GMID"
    assert not any("cascode tail" in w for w in r.warnings)


def test_ptm_without_lut_raises_unsupported():
    """A PTM/SPICE-model node with no gm/Id LUT must error, not fall through to
    the Level-1 square-law sizer (ptm32/22/16 have a spice_model but no LUT)."""
    from circuitgenome.sizer import UnsupportedTechError
    mods = load_modules()
    topo = next(t for t in load_topologies() if t.name == _TOPO)
    circ = next(enumerate_circuits(topo, mods))
    parsed = parse(to_flat_spice(circ))
    fbr = assign_slots(recognize(parsed), topo)
    with pytest.raises(UnsupportedTechError):
        size_circuit(parsed, recognize(parsed), fbr, topo, load_tech("ptm32"), _spec())


def test_level1_path_bias_feasible_default_true():
    """The Level-1 (generic) path never runs the DC-op check → bias_feasible=True."""
    mods = load_modules()
    topo = next(t for t in load_topologies() if t.name == _TOPO)
    circ = next(enumerate_circuits(topo, mods))
    parsed = parse(to_flat_spice(circ))
    fbr = assign_slots(recognize(parsed), topo)
    spec = SizingSpec(vdd=5.0, vss=0.0, ibias=10e-6, cl=20e-12,
                      second_stage_current_ratio=2.5, gain_min_db=40,
                      gbw_min_hz=2.5e6, phase_margin_min_deg=60, slew_rate_min_vps=3.5e6)
    r = size_circuit(parsed, recognize(parsed), fbr, topo, load_tech("generic"), spec)
    assert r.solver_status in ("OPTIMAL", "FEASIBLE")
    assert r.bias_feasible is True
