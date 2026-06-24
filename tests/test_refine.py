"""Tests for SPICE-in-the-loop op-point refinement (issue #76, cause A)."""
import pytest

from circuitgenome.recognizer import assign_slots, parse, recognize
from circuitgenome.sizer.device_model import build_device_model
from circuitgenome.sizer.loader import load_tech
from circuitgenome.sizer.models import SizingSpec
from circuitgenome.sizer.refine import refine_with_spice
from circuitgenome.sizer.sizer import _extract_slot_transistors, size_circuit
from circuitgenome.sizer.spice_sim import ngspice_available
from circuitgenome.synthesizer.loader import load_modules, load_topologies
from circuitgenome.synthesizer.netlist import to_flat_spice
from circuitgenome.synthesizer.synthesizer import enumerate_circuits

pytestmark = pytest.mark.skipif(not ngspice_available(), reason="ngspice not on PATH")


def _active_load_two_stage():
    mods = load_modules()
    topo = next(t for t in load_topologies()
                if t.name == "two_stage_opamp_single_ended")
    vf = {"input_pair": "differential_pair_pmos", "load": "active_load_nmos",
          "tail_current": "current_mirror_tail_pmos",
          "bias_gen": "diode_connected_mosfet_bias"}
    c = next(c for c in enumerate_circuits(topo, mods)
             if all(c.variant_map.get(k).name == v for k, v in vf.items()))
    net = to_flat_spice(c)
    parsed = parse(net)
    fbr = assign_slots(recognize(parsed), topo)
    return net, parsed, fbr, topo


def test_refine_tracks_spice_op_point():
    net, parsed, fbr, topo = _active_load_two_stage()
    tech = load_tech("ptm45")
    spec = SizingSpec(vdd=1.0, vss=0.0, ibias=20e-6, cl=2e-12,
                      second_stage_current_ratio=2.5, gain_min_db=50,
                      gbw_min_hz=5e6, phase_margin_min_deg=60, slew_rate_min_vps=5e6)
    r = size_circuit(parsed, recognize(parsed), fbr, topo, tech, spec)
    slot = _extract_slot_transistors(fbr)
    gdr = (1.0 / min(r.resistors.values())) if r.resistors else 0.0
    rr = refine_with_spice(r, net, slot, tech, spec, build_device_model(tech), gdr)

    assert any("refinement" in w for w in rr.warnings)
    # The headroom-starved tail collapses the bias current, so the refined GBW
    # is far below the (optimistic) analytical value.
    assert rr.metrics["gbw_hz"] < 0.5 * r.metrics["gbw_hz"]
    # Refined currents reflect the actual (reduced) operating point.
    ip = next(ref for ref in rr.transistors if "input_pair" in ref)
    assert rr.transistors[ip].ids_a < r.transistors[ip].ids_a
