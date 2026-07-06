"""Tests for the gm/Id functional-block view (circuitgenome/sizer/gmid/blocks)."""
from circuitgenome.sizer.gmid.blocks import (
    LoadKind,
    build_blocks,
    classify_load,
)
from circuitgenome.sizer.shared.taxonomy import is_signal_device
from circuitgenome.synthesizer.models import Device


def D(ref, t, **term):
    return Device(ref=ref, type=t, terminals=term)


def test_classify_load_kinds():
    mirror = [D("m1", "nmos", g="x", d="x", s="0"),   # diode-connected
              D("m2", "nmos", g="x", d="y", s="0")]
    cascode = [D("m1", "nmos", g="b1", d="n1", s="0"),
               D("m2", "nmos", g="b2", d="y", s="n1")]  # stacked: s == m1.d
    cs = [D("m1", "nmos", g="net_bias1", d="y", s="0")]
    res = [D("r1", "resistor", a="y", b="0")]
    assert classify_load(mirror, []) == LoadKind.MIRROR
    assert classify_load(cascode, []) == LoadKind.CASCODE
    assert classify_load(cs, []) == LoadKind.CURRENT_SOURCE
    assert classify_load([], res) == LoadKind.RESISTOR
    assert classify_load([], []) == LoadKind.NONE


def test_is_signal_device():
    assert is_signal_device(D("m", "pmos", g="in1", d="o", s="t"))
    assert not is_signal_device(D("m", "pmos", g="net_bias1", d="o", s="t"))
    assert not is_signal_device(D("m", "nmos", g="vdd!", d="o", s="0"))


def test_build_blocks_and_first_stage_factor():
    ip = [D("m1_input_pair", "pmos", g="in1", d="o1", s="t"),
          D("m2_input_pair", "pmos", g="in2", d="o2", s="t")]
    mirror_load = [D("m1_load", "nmos", g="x", d="x", s="0"),
                   D("m2_load", "nmos", g="x", d="o2", s="0")]
    # mirror first-stage load → full gain (k_fs = 1.0)
    b = build_blocks({"input_pair": ip, "load": mirror_load}, {})
    assert b.n_stages == 1
    assert not b.is_fully_differential
    assert b.load.load_kind == LoadKind.MIRROR
    assert b.first_stage_gain_factor() == 1.0

    # resistor load → single-ended halving (k_fs = 0.5)
    b2 = build_blocks({"input_pair": ip}, {"load": [D("r1_load", "resistor", a="o2", b="0")]})
    assert b2.load.load_kind == LoadKind.RESISTOR
    assert b2.first_stage_gain_factor() == 0.5


def test_fully_differential_factor():
    b = build_blocks({"input_pair": [], "second_stage_p": [], "second_stage_n": []}, {})
    assert b.is_fully_differential and b.n_stages == 2
    assert b.first_stage_gain_factor() == 1.0


def test_has_cascode_tail():
    ip = [D("mi1", "nmos", g="in1", d="o1", s="t"),
          D("mi2", "nmos", g="in2", d="o2", s="t")]
    # Plain single-device tail: the tail slot is never classified, so this must
    # be detected structurally, not via load_kind (issue #145).
    plain = build_blocks(
        {"input_pair": ip, "tail_current": [D("mt", "nmos", g="net_bias1", d="t", s="0")]}, {})
    assert not plain.has_cascode_tail()
    # Cascode tail: top device stacked on the bottom device's drain.
    casc = build_blocks(
        {"input_pair": ip,
         "tail_current": [D("mt_top", "nmos", g="net_bias2", d="t", s="tc"),
                          D("mt_bot", "nmos", g="net_bias1", d="tc", s="0")]}, {})
    assert casc.has_cascode_tail()
    # No tail slot at all → not a cascode tail (resistor-tail / degenerate case).
    assert not build_blocks({"input_pair": ip}, {}).has_cascode_tail()
