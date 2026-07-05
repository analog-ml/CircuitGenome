"""Tests for gm/Id resistor-block sizing (degeneration / tail / bias)."""
import math

import pytest

from circuitgenome.recognizer import assign_slots, parse, recognize
from circuitgenome.sizer.gmid.blocks import build_blocks
from circuitgenome.sizer.gmid.intent import GmIdIntent
from circuitgenome.sizer.gmid.resistors import size_resistors
from circuitgenome.sizer.shared.loader import load_tech
from circuitgenome.sizer.shared.models import SizingSpec, TransistorSizing
from circuitgenome.sizer.sizer import size_circuit
from circuitgenome.synthesizer.loader import load_modules, load_topologies
from circuitgenome.synthesizer.netlist import to_flat_spice
from circuitgenome.synthesizer.synthesizer import enumerate_circuits

_TOPO = "two_stage_opamp_single_ended"
_BASE = dict(load="active_load_nmos", tail_current="current_mirror_tail_pmos",
             second_stage="common_source_nmos", compensation="miller_cap")


def _spec(gain=40):
    return SizingSpec(vdd=1.0, vss=0.0, ibias=15e-6, cl=2e-12,
                      second_stage_current_ratio=2.5, gain_min_db=gain,
                      gbw_min_hz=2e6, phase_margin_min_deg=60, slew_rate_min_vps=1e6,
                      output_swing_max_v=0.8, output_swing_min_v=0.2)


def _size(**variant):
    mods = load_modules()
    topo = next(t for t in load_topologies() if t.name == _TOPO)
    want = {**_BASE, **variant}
    c = next(c for c in enumerate_circuits(topo, mods)
             if all(c.variant_map.get(k) and c.variant_map.get(k).name == v
                    for k, v in want.items()))
    parsed = parse(to_flat_spice(c))
    fbr = assign_slots(recognize(parsed), topo)
    return size_circuit(parsed, recognize(parsed), fbr, topo, load_tech("ptm45"), _spec())


# --- unit: degeneration arithmetic ----------------------------------------
class _FakeModel:
    def gm(self, dtype, w, l, ids):
        return 1e-3   # 1 mS

    def vgs(self, dtype, w, l, ids):
        return 0.5


def _sz(ref, t="pmos"):
    return TransistorSizing(ref=ref, w_um=1, l_um=0.1, ids_a=5e-6, vgs_v=0.5, vds_sat_v=0.1)


def test_size_resistors_degeneration_factor():
    from circuitgenome.synthesizer.models import Device
    ip = [Device(ref="m1", type="pmos", terminals={"d": "o1", "g": "in1", "s": "s1"}),
          Device(ref="m2", type="pmos", terminals={"d": "o2", "g": "in2", "s": "s2"})]
    rdev = [Device(ref="r1", type="resistor", terminals={"t1": "s1", "t2": "tail"}),
            Device(ref="r2", type="resistor", terminals={"t1": "s2", "t2": "tail"})]
    blocks = build_blocks({"input_pair": ip}, {"input_pair": rdev})
    sizing = {"m1": _sz("m1"), "m2": _sz("m2")}
    intent = GmIdIntent(degeneration_factor=0.5)
    out, modifiers = size_resistors(
        blocks, {"input_pair": rdev}, {}, sizing, _FakeModel(),
        _spec(), load_tech("ptm45"), intent)
    assert out["r1"] == pytest.approx(0.5 / 1e-3)     # R = factor/gm1 = 500 Ω
    assert modifiers.gm1_factor == pytest.approx(1.0 / 1.5)
    assert modifiers.gd_tail_override is None
    assert modifiers.gd_out_extra == 0.0   # no CMFB sense resistors here


# --- unit: tunable bias-leg rail values (issue #100) -----------------------
def _casc_sz(ref, vgs, vdsat):
    return TransistorSizing(ref=ref, w_um=1, l_um=0.1, ids_a=15e-6,
                            vgs_v=vgs, vds_sat_v=vdsat)


def _bias_leg_r(blocks_mosfets, sizing, rail="net_bias2"):
    """Run size_resistors with one tunable bias-leg resistor on *rail*."""
    from circuitgenome.synthesizer.models import Device
    r = Device(ref="r2_bias_gen", type="resistor", terminals={"t1": rail, "t2": "gnd!"})
    blocks = build_blocks(blocks_mosfets, {"bias_gen": [r]})
    out, _ = size_resistors(blocks, {"bias_gen": [r]}, {}, sizing, _FakeModel(),
                            _spec(), load_tech("ptm45"), GmIdIntent())
    return out["r2_bias_gen"]


def test_tunable_leg_nmos_cascode_rail():
    """NMOS cascode consumer: rail = Vdsat(bottom) + margin + Vgs(cascode)
    above gnd."""
    from circuitgenome.synthesizer.models import Device
    casc = Device(ref="mn1_load", type="nmos",
                  terminals={"d": "net_x", "g": "net_bias2", "s": "net_fold"})
    bottom = Device(ref="mn3_load", type="nmos",
                    terminals={"d": "net_fold", "g": "net_bias1", "s": "gnd!"})
    from circuitgenome.sizer.gmid.resistors import _CASCODE_SAT_MARGIN_V as m
    sizing = {"mn1_load": _casc_sz("mn1_load", 0.45, 0.12),
              "mn3_load": _casc_sz("mn3_load", 0.40, 0.15)}
    r = _bias_leg_r({"load": [casc, bottom]}, sizing)
    assert r == pytest.approx((0.15 + m + 0.45) / 15e-6)


def test_tunable_leg_pmos_cascode_anchors_at_input_pair():
    """Telescopic-style PMOS cascode on the input-pair drain: the walk anchors
    at the input device's saturation edge with its gate at Vcm."""
    from circuitgenome.synthesizer.models import Device
    casc = Device(ref="mp1_load", type="pmos",
                  terminals={"d": "net_out", "g": "net_bias2", "s": "net_in1"})
    ip = Device(ref="m1_input_pair", type="pmos",
                terminals={"d": "net_in1", "g": "vin", "s": "net_tail"})
    from circuitgenome.sizer.gmid.resistors import _CASCODE_SAT_MARGIN_V as m
    sizing = {"mp1_load": _casc_sz("mp1_load", 0.50, 0.10),
              "m1_input_pair": _casc_sz("m1_input_pair", 0.40, 0.10)}
    r = _bias_leg_r({"load": [casc], "input_pair": [ip]}, sizing)
    # Vcm + (|Vgs_ip| - |Vdsat_ip| - margin) - |Vgs_casc| = 0.5 + (0.3 - m) - 0.5
    assert r == pytest.approx((0.3 - m) / 15e-6)


def test_tunable_leg_conflicting_supply_gates_take_mean():
    """A shared rail with an NMOS gnd-referenced and a PMOS vdd-referenced
    gate gets the mean of the two demands."""
    from circuitgenome.synthesizer.models import Device
    n = Device(ref="mn1_load", type="nmos",
               terminals={"d": "net_a", "g": "net_bias2", "s": "gnd!"})
    p = Device(ref="mp1_second_stage", type="pmos",
               terminals={"d": "net_b", "g": "net_bias2", "s": "vdd!"})
    sizing = {"mn1_load": _casc_sz("mn1_load", 0.40, 0.10),
              "mp1_second_stage": _casc_sz("mp1_second_stage", 0.50, 0.10)}
    r = _bias_leg_r({"load": [n], "second_stage": [p]}, sizing)
    # mean(0.40, 1.0 - 0.50) = 0.45 V
    assert r == pytest.approx(0.45 / 15e-6)            # 30 kΩ


def test_tunable_leg_fallback_when_no_level_derivable():
    """A diode-connected consumer is a current interface — no voltage demand;
    with no bias_gen MOSFETs either, the half-supply fallback applies."""
    from circuitgenome.synthesizer.models import Device
    diode = Device(ref="m1_tail_current", type="nmos",
                   terminals={"d": "net_bias2", "g": "net_bias2", "s": "gnd!"})
    sizing = {"m1_tail_current": _casc_sz("m1_tail_current", 0.40, 0.10)}
    r = _bias_leg_r({"tail_current": [diode]}, sizing)
    assert r == pytest.approx(0.5 * 1.0 / 15e-6)       # half-supply / ibias


# --- integration ----------------------------------------------------------
def test_degeneration_reduces_gain():
    plain = _size(input_pair="differential_pair_pmos").metrics["gain_db"]
    degen = _size(input_pair="differential_pair_pmos_degenerated").metrics["gain_db"]
    assert plain - degen == pytest.approx(20 * math.log10(1.5), abs=0.2)


def test_resistor_tail_and_bias_sized():
    rt = _size(tail_current="resistor_tail_vdd")
    assert any("tail_current" in ref for ref in rt.resistors)
    assert all(v > 1e3 for v in rt.resistors.values())   # not the 1 kΩ placeholder

    # A cascode-consumer rail (folded-cascode bias2) gets a cascode_gnd
    # level leg: the diode covers the consumer's Vgs, the floor resistor
    # only the stack's Vdsat floor (floor/ibias -- ~kΩ to tens of kΩ, well
    # below the retired whole-level value of Vgs-plus-floor over ibias).
    rb = _size(load="folded_cascode_load_pmos_input_single_output")
    assert any("bias_gen" in ref for ref in rb.resistors)
    r_leg = next(v for k, v in rb.resistors.items() if "bias_gen" in k)
    assert 1e3 < r_leg < 3e4


def test_no_resistors_for_active_circuit():
    """An all-active circuit gets no extra (degeneration/tail/bias) resistors."""
    r = _size(input_pair="differential_pair_pmos")
    assert r.resistors == {}


# --- compensation resistors (issue #108) -----------------------------------
def test_comp_resistor_zero_on_output_pole():
    """Nulling R = (Cc+CL)/(gm2·Cc) places the compensation zero on the
    output pole."""
    from circuitgenome.synthesizer.models import Device
    ss = [Device(ref="m1_second_stage", type="nmos",
                 terminals={"d": "out", "g": "net_mid", "s": "gnd!"})]
    r = Device(ref="r1_compensation", type="resistor",
               terminals={"t1": "net_mid", "t2": "cn"})
    blocks = build_blocks({"second_stage": ss}, {"compensation": [r]})
    sizing = {"m1_second_stage": _sz("m1_second_stage", "nmos")}
    out, _ = size_resistors(
        blocks, {"compensation": [r]}, {}, sizing, _FakeModel(),
        _spec(), load_tech("ptm45"), GmIdIntent(), cc_pf=2.0)
    # (2p + 2p) / (1 mS · 2p) = 2 kΩ
    assert out["r1_compensation"] == pytest.approx((2e-12 + 2e-12) / (1e-3 * 2e-12))


@pytest.mark.parametrize("variant", ["miller_cap_with_nulling_resistor",
                                     "indirect_compensation"])
def test_comp_resistor_sized_end_to_end(variant):
    """Compensation-slot resistors get a deliberate value, not the 1 kΩ
    placeholder, and flow out through SizingResult.resistors."""
    res = _size(compensation=variant)
    comp_r = {k: v for k, v in res.resistors.items() if "comp" in k}
    assert comp_r, f"{variant}: compensation resistor not sized"
    cc_f = res.cc_pf * 1e-12
    for v in comp_r.values():
        assert v > 0 and v != pytest.approx(1e3, rel=1e-6)
        # zero placement is bounded by the stage gm: R·Cc/(Cc+CL) = 1/gm2
        gm2 = (cc_f + _spec().cl) / (v * cc_f)
        assert 1e-5 < gm2 < 1e-1   # µS–tens-of-mS: a physical stage gm
