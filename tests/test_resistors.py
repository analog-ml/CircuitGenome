"""Tests for gm/Id resistor-block sizing (degeneration / tail / bias)."""
import math

import pytest

from circuitgenome.recognizer import assign_slots, parse, recognize
from circuitgenome.sizer.gmid.blocks import build_blocks
from circuitgenome.sizer.gmid.intent import GmIdIntent
from circuitgenome.sizer.gmid.resistors import size_resistors
from circuitgenome.sizer.loader import load_tech
from circuitgenome.sizer.models import SizingSpec, TransistorSizing
from circuitgenome.sizer.sizer import size_circuit
from circuitgenome.synthesizer.loader import load_modules, load_topologies
from circuitgenome.synthesizer.netlist import to_flat_spice
from circuitgenome.synthesizer.synthesizer import enumerate_circuits

_TOPO = "two_stage_opamp_single_ended"
_BASE = dict(load="active_load_nmos", tail_current="current_mirror_tail_pmos",
             second_stage="common_source", bias_gen="diode_connected_mosfet_bias",
             compensation="miller_cap")


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
    out, gm1_factor, gd_tail, gd_out_extra = size_resistors(
        blocks, {"input_pair": rdev}, {}, sizing, _FakeModel(),
        _spec(), load_tech("ptm45"), intent)
    assert out["r1"] == pytest.approx(0.5 / 1e-3)     # R = factor/gm1 = 500 Ω
    assert gm1_factor == pytest.approx(1.0 / 1.5)
    assert gd_tail is None
    assert gd_out_extra == 0.0   # no CMFB sense resistors here


# --- integration ----------------------------------------------------------
def test_degeneration_reduces_gain():
    plain = _size(input_pair="differential_pair_pmos").metrics["gain_db"]
    degen = _size(input_pair="differential_pair_pmos_degenerated").metrics["gain_db"]
    assert plain - degen == pytest.approx(20 * math.log10(1.5), abs=0.2)


def test_resistor_tail_and_bias_sized():
    rt = _size(tail_current="resistor_tail_vdd")
    assert any("tail_current" in ref for ref in rt.resistors)
    assert all(v > 1e3 for v in rt.resistors.values())   # not the 1 kΩ placeholder

    rb = _size(bias_gen="resistor_bias")
    assert any("bias_gen" in ref for ref in rb.resistors)
    assert all(v > 1e4 for v in rb.resistors.values())   # ~Vgs/ibias, tens of kΩ


def test_no_resistors_for_active_circuit():
    """An all-active circuit gets no extra (degeneration/tail/bias) resistors."""
    r = _size(input_pair="differential_pair_pmos")
    assert r.resistors == {}
