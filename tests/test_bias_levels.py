"""Tests for the constructed-bias level tuning (sizer/gmid/bias_levels.py)."""
import pytest

from circuitgenome.recognizer import assign_slots, parse, recognize
from circuitgenome.sizer.shared.loader import load_tech
from circuitgenome.sizer.shared.models import SizingSpec
from circuitgenome.sizer.sizer import size_circuit
from circuitgenome.synthesizer.loader import load_modules, load_topologies
from circuitgenome.synthesizer.netlist import to_flat_spice
from circuitgenome.synthesizer.synthesizer import enumerate_circuits

_TOPO = "two_stage_opamp_single_ended"
_BASE = dict(input_pair="differential_pair_pmos",
             tail_current="current_mirror_tail_pmos",
             second_stage="common_source_nmos", compensation="miller_cap")


def _spec(vdd=1.0):
    return SizingSpec(vdd=vdd, vss=0.0, ibias=15e-6, cl=2e-12,
                      second_stage_current_ratio=2.5, gain_min_db=40,
                      gbw_min_hz=2e6, phase_margin_min_deg=60,
                      slew_rate_min_vps=1e6,
                      output_swing_max_v=0.8 * vdd, output_swing_min_v=0.2 * vdd)


def _size(tech="ptm45", vdd=1.0, **variant):
    mods = load_modules()
    topo = next(t for t in load_topologies() if t.name == _TOPO)
    want = {**_BASE, **variant}
    c = next(c for c in enumerate_circuits(topo, mods)
             if all(c.variant_map.get(k) and c.variant_map.get(k).name == v
                    for k, v in want.items()))
    parsed = parse(to_flat_spice(c))
    fbr = assign_slots(recognize(parsed), topo)
    return size_circuit(parsed, recognize(parsed), fbr, topo,
                        load_tech(tech), _spec(vdd))


def test_cascode_gnd_leg_diode_tracks_consumer_vgs():
    """The folded-cascode bias2 leg's level diode is re-sized so its Vgs
    matches the consumer cascode's planned Vgs, and the floor resistor
    covers exactly the stack's Vdsat floor plus the saturation margin
    (mirror-consistent: computed from the diode's post-tuning Vgs)."""
    from circuitgenome.sizer.gmid.resistors import _CASCODE_SAT_MARGIN_V as m
    res = _size(load="folded_cascode_load_pmos_input_single_output")
    diode = res.transistors["mn2_bias_gen"]
    consumer = res.transistors["mn1_load"]
    bottom = res.transistors["mn3_load"]
    assert diode.vgs_v == pytest.approx(consumer.vgs_v, abs=0.05)
    target = consumer.vgs_v + bottom.vds_sat_v + m
    floor = target - diode.vgs_v
    assert res.resistors["r2_bias_gen"] == pytest.approx(floor / 15e-6, rel=0.02)


def test_cascode_vdd_leg_reaches_telescopic_anchor():
    """The telescopic bias1 rail lands at the input-pair anchor (a
    saturation margin inside the pair's edge) minus the cascode's |Vgs|:
    vdd - |Vgs(diode)| - I*R equals the target the consumer stack walk
    derives."""
    from circuitgenome.sizer.gmid.resistors import _CASCODE_SAT_MARGIN_V as m
    res = _size(load="telescopic_cascode_load_pmos")
    diode = res.transistors["mp1_bias_gen"]
    casc = res.transistors["mp1_load"]
    ip = res.transistors["m1_input_pair"]
    spec = _spec()
    anchor = (spec.vdd + spec.vss) / 2 + (abs(ip.vgs_v) - abs(ip.vds_sat_v) - m)
    target = anchor - abs(casc.vgs_v)
    rail = spec.vdd - abs(diode.vgs_v) - 15e-6 * res.resistors["r1_bias_gen"]
    assert rail == pytest.approx(target, abs=0.02)


def test_wideswing_telescopic_bias2_leg_tracks_mirror_cascode():
    """The wide-swing telescopic load's bias2 rail (issue #129) is a
    cascode_gnd leg driving the NMOS mirror cascodes (mn3/mn4). Its level
    diode's Vgs tracks the consumer cascode's planned Vgs (mirror-consistent
    level), and the rail lands above that Vgs by a positive floor resistor
    drop — i.e. at Vgs(cascode) plus the bottom mirror device's saturation
    headroom, well short of the two-diode Vgs+Vgs the self-biased load spends."""
    res = _size(load="telescopic_cascode_load_wideswing_pmos")
    assert res.solver_status == "GMID"
    diode = res.transistors["mn2_bias_gen"]
    casc = res.transistors["mn3_load"]
    assert diode.vgs_v == pytest.approx(casc.vgs_v, abs=0.05)
    rail = abs(diode.vgs_v) + 15e-6 * res.resistors["r2_bias_gen"]
    # A real floor above the cascode Vgs, but nowhere near a second full Vgs.
    assert abs(casc.vgs_v) < rail < 2 * abs(casc.vgs_v)


def test_pref_cascode_pins_mirror_below_master_vds():
    """The pref branch's ncasc level pins the mirror's drain (ncasc Vgs
    minus cascode Vgs) between the mirror's own Vdsat (saturated) and the
    master's Vgs (never overshooting the Vds-match target), instead of the
    uncascoded vdd - |VGSP| ~ 4%-error operating point."""
    res = _size(load="folded_cascode_load_pmos_input_single_output",
                tech="gf180mcu", vdd=3.3)
    ncasc = res.transistors["mncdio_bias_gen"]
    casc = res.transistors["mncasc_bias_gen"]
    mirror = res.transistors["mnpref_bias_gen"]
    master = res.transistors["mnref_bias_gen"]
    n1 = ncasc.vgs_v - casc.vgs_v
    assert n1 > mirror.vds_sat_v          # mirror stays saturated
    assert n1 <= master.vgs_v + 0.01      # never overshoots the Vds match
    # and the level actually moved: the ncasc diode is narrower/stronger
    # than the intent-table default it would share with the mirror.
    assert ncasc.vgs_v > mirror.vgs_v + 0.05


def test_pref_cascode_clamps_out_when_supply_has_no_headroom():
    """When the supply leaves no room under the pref node (gf180 Vth at a
    1 V supply), the tuner leaves the intent-table sizing untouched rather
    than forcing an infeasible level."""
    res = _size(load="folded_cascode_load_pmos_input_single_output",
                tech="gf180mcu", vdd=1.0)
    ncasc = res.transistors["mncdio_bias_gen"]
    mirror = res.transistors["mnpref_bias_gen"]
    assert ncasc.vgs_v == pytest.approx(mirror.vgs_v)


def test_no_constructed_bias_shapes_is_a_noop():
    """A circuit whose bias generator has no cascode legs and no pref
    cascode (plain active load, PMOS mirror tail) sizes exactly as before —
    no bias_gen resistors, no level retuning."""
    res = _size(load="active_load_nmos")
    assert not any("bias_gen" in ref for ref in res.resistors)
