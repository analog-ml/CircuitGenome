"""gm/Id coverage for fully-differential and three-stage op-amps (issue #75)."""
import pytest

from circuitgenome.recognizer import assign_slots, parse, recognize
from circuitgenome.sizer.shared.loader import load_tech
from circuitgenome.sizer.shared.models import SizingSpec
from circuitgenome.sizer.sizer import size_circuit
from circuitgenome.synthesizer.loader import load_modules, load_topologies
from circuitgenome.synthesizer.netlist import to_flat_spice
from circuitgenome.synthesizer.synthesizer import enumerate_circuits


def _size(topo_name, want, spec):
    mods = load_modules()
    topo = next(t for t in load_topologies() if t.name == topo_name)
    circ = next(c for c in enumerate_circuits(topo, mods)
                if all(c.variant_map.get(k) and c.variant_map.get(k).name == v
                       for k, v in want.items()))
    parsed = parse(to_flat_spice(circ))
    fbr = assign_slots(recognize(parsed), topo)
    return size_circuit(parsed, recognize(parsed), fbr, topo, load_tech("ptm45"), spec)


_FD_SPEC = SizingSpec(vdd=1.0, vss=0.0, ibias=15e-6, cl=2e-12,
                      second_stage_current_ratio=2.5, gain_min_db=50,
                      gbw_min_hz=2e6, phase_margin_min_deg=60, slew_rate_min_vps=1e6,
                      output_swing_max_v=0.8, output_swing_min_v=0.2)
_TS_SPEC = SizingSpec(vdd=1.0, vss=0.0, ibias=15e-6, cl=2e-12,
                      second_stage_current_ratio=2.5, third_stage_current_ratio=5.0,
                      gain_min_db=60, gbw_min_hz=2e6, phase_margin_min_deg=60,
                      slew_rate_min_vps=1e6, output_swing_max_v=0.8, output_swing_min_v=0.2)

_FD_LOAD = "folded_cascode_load_pmos_input_differential_output"
# This consumer set mixes bias-rail flavors (rail 1/4 gnd, rail 5/7 vdd), so
# only resistor_bias survives the flavor filter (bias_compatibility.py).
_FD_BASE = {"input_pair": "differential_pair_pmos", "load": _FD_LOAD,
            "tail_current": "current_mirror_tail_pmos",
            "bias_gen": "resistor_bias",
            "comp_p": "miller_cap", "comp_n": "miller_cap",
            "second_stage_p": "common_source", "second_stage_n": "common_source"}


@pytest.mark.parametrize("cmfb", ["resistive_sense_cmfb", "dda_cmfb"])
def test_fd_two_stage_gmid(cmfb):
    r = _size("two_stage_opamp_fully_differential", {**_FD_BASE, "cmfb": cmfb}, _FD_SPEC)
    assert r.solver_status == "GMID"
    assert r.transistors and r.cc_pf
    assert "gain_db" in r.metrics and r.metrics["gain_db"] > 0
    if cmfb == "resistive_sense_cmfb":
        # CMFB sense resistors are sized large (not the 1 kΩ placeholder).
        cmfb_r = [v for k, v in r.resistors.items() if "cmfb" in k]
        assert cmfb_r and all(v > 1e5 for v in cmfb_r)


@pytest.mark.parametrize("topo,load,extra", [
    ("three_stage_opamp_nmc_single_ended", "folded_cascode_load_pmos_input_single_output", {}),
    ("three_stage_opamp_rnmc_single_ended", "folded_cascode_load_pmos_input_single_output", {}),
])
def test_three_stage_se_gmid(topo, load, extra):
    # fc_pmos_single's bias1 is gnd-flavored, the tail and stages vdd-flavored
    # -- mixed, so only resistor_bias survives the flavor filter.
    want = {"input_pair": "differential_pair_pmos", "load": load,
            "tail_current": "current_mirror_tail_pmos",
            "bias_gen": "resistor_bias",
            "second_stage": "common_source", "third_stage": "common_source",
            "comp1": "miller_cap", "comp2": "miller_cap", **extra}
    r = _size(topo, want, _TS_SPEC)
    assert r.solver_status == "GMID"
    assert r.transistors and r.cc_pf and r.cc2_pf  # three-stage inner cap set
    assert r.metrics.get("gain_db", 0) > 0
