"""Quiescent node-level estimate the bias-aware cascode walk reads (issue #273)."""
import pytest

from circuitgenome.sizer import TransistorSizing
from circuitgenome.sizer.physics.node_levels import device_bias, estimate_node_levels
from circuitgenome.synthesizer.models import Device

_RAILS = {"vdd!": 3.3, "gnd!": 0.0}


class _Model:
    """Body shift linear in Vsb (``k·Vsb``) so fixed points are solvable by hand."""
    def __init__(self, k=0.0):
        self.k = k

    def body_vth_shift(self, dtype, vsb):
        return self.k * vsb


def D(ref, t, **term):
    return Device(ref=ref, type=t, terminals=term)


def _sz(ref, vgs, ids=10e-6):
    return TransistorSizing(ref=ref, w_um=1.0, l_um=1.0, ids_a=ids, vgs_v=vgs,
                            vds_sat_v=0.15)


def test_known_gate_sets_the_source_and_known_source_sets_the_gate():
    """A cascode's source sits one Vgs under its gate; a diode's node one
    Vgs over its source -- here an NMOS and a PMOS each way."""
    devs = [D("mn_dio", "nmos", d="nb", g="nb", s="gnd!", b="gnd!"),
            D("mn_cas", "nmos", d="out", g="nb", s="x", b="x"),
            D("mp_dio", "pmos", d="pb", g="pb", s="vdd!", b="vdd!"),
            D("mp_cas", "pmos", d="out", g="pb", s="y", b="y")]
    sizing = {"mn_dio": _sz("mn_dio", 0.8), "mn_cas": _sz("mn_cas", 0.6),
              "mp_dio": _sz("mp_dio", -0.9), "mp_cas": _sz("mp_cas", -0.7)}
    lv = estimate_node_levels(devs, [], {}, sizing, _Model(), dict(_RAILS))
    assert lv["nb"] == pytest.approx(0.8)
    assert lv["x"] == pytest.approx(0.2)
    assert lv["pb"] == pytest.approx(2.4)
    assert lv["y"] == pytest.approx(3.1)
    assert "out" not in lv                      # nothing pins a drain-only net
    assert device_bias(devs[1], lv) == (None, pytest.approx(0.0))


def test_floor_resistor_drops_its_leg_current():
    """``cascode_gnd`` bias leg: the diode rides ``I·R`` above ground, so the
    rail it makes -- and the source of the cascode it gates -- carry the floor."""
    devs = [D("mn_leg", "nmos", d="rail", g="rail", s="mid", b="gnd!"),
            D("mn_bot", "nmos", d="n1", g="vb", s="gnd!", b="gnd!"),
            D("mn_cas", "nmos", d="out", g="rail", s="n1", b="gnd!")]
    r = D("r_leg", "resistor", t1="mid", t2="gnd!")
    sizing = {"mn_leg": _sz("mn_leg", 0.7, ids=20e-6),
              "mn_bot": _sz("mn_bot", 0.7), "mn_cas": _sz("mn_cas", 0.7)}
    lv = estimate_node_levels(devs, [r], {"r_leg": 1e4}, sizing, _Model(),
                              dict(_RAILS))
    assert lv["mid"] == pytest.approx(0.2)      # 20 µA · 10 kΩ
    assert lv["rail"] == pytest.approx(0.9)
    assert lv["n1"] == pytest.approx(0.2)       # the device below sees the floor
    vds, vsb = device_bias(devs[1], lv)
    assert vds == pytest.approx(0.2) and vsb == pytest.approx(0.0)


def test_unsized_resistor_drops_nothing():
    devs = [D("mn_leg", "nmos", d="rail", g="rail", s="mid", b="gnd!")]
    r = D("r_leg", "resistor", t1="mid", t2="gnd!")
    lv = estimate_node_levels(devs, [r], {}, {"mn_leg": _sz("mn_leg", 0.7)},
                              _Model(), dict(_RAILS))
    assert "mid" not in lv and "rail" not in lv


def test_degeneration_resistor_reads_its_current_at_the_pair_source():
    """Two resistors meet on the tail node, so each one's current is read at
    its pair-source end: a PMOS pair's tail sits ``I·R`` above its source."""
    devs = [D("m1", "pmos", d="d1", g="in1", s="s1", b="s1"),
            D("m2", "pmos", d="d2", g="in2", s="s2", b="s2")]
    rs = [D("r1", "resistor", t1="s1", t2="tail"),
          D("r2", "resistor", t1="s2", t2="tail")]
    sizing = {"m1": _sz("m1", -0.8), "m2": _sz("m2", -0.8)}
    pinned = {**_RAILS, "in1": 1.65, "in2": 1.65}
    lv = estimate_node_levels(devs, rs, {"r1": 5e3, "r2": 5e3}, sizing, _Model(),
                              pinned)
    assert lv["s1"] == pytest.approx(2.45)
    assert lv["tail"] == pytest.approx(2.45 + 10e-6 * 5e3)


def test_body_effect_raises_a_cascode_vgs():
    """Source off the bulk: Vs = Vg − (Vgs + k·Vs), so Vs = (Vg − Vgs)/(1 + k)."""
    devs = [D("mn_cas", "nmos", d="out", g="g", s="x", b="gnd!")]
    lv = estimate_node_levels(devs, [], {}, {"mn_cas": _sz("mn_cas", 0.6)},
                              _Model(k=0.1), {**_RAILS, "g": 1.7})
    assert lv["x"] == pytest.approx(1.1 / 1.1, rel=1e-3)


def test_pinned_levels_are_never_overwritten():
    devs = [D("mn", "nmos", d="a", g="a", s="gnd!", b="gnd!")]
    lv = estimate_node_levels(devs, [], {}, {"mn": _sz("mn", 0.8)}, _Model(),
                              {**_RAILS, "a": 1.0})
    assert lv["a"] == 1.0
