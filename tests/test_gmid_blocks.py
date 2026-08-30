"""Tests for the gm/Id functional-block view (circuitgenome/sizer/gmid/analyze)."""
from circuitgenome.sizer.gmid.analyze import (
    LoadKind,
    build_blocks,
    classify_load,
)
from circuitgenome.sizer.physics.taxonomy import is_signal_device
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


def test_build_blocks_load_kind():
    ip = [D("m1_input_pair", "pmos", g="in1", d="o1", s="t"),
          D("m2_input_pair", "pmos", g="in2", d="o2", s="t")]
    mirror_load = [D("m1_load", "nmos", g="x", d="x", s="0"),
                   D("m2_load", "nmos", g="x", d="o2", s="0")]
    b = build_blocks({"input_pair": ip, "load": mirror_load}, {})
    assert not b.is_fully_differential
    assert b.load.load_kind == LoadKind.MIRROR

    # A resistor-only load slot still gets a block.
    b2 = build_blocks({"input_pair": ip}, {"load": [D("r1_load", "resistor", a="o2", b="0")]})
    assert b2.load.load_kind == LoadKind.RESISTOR


def test_fully_differential_flag():
    b = build_blocks({"input_pair": [], "second_stage_p": [], "second_stage_n": []}, {})
    assert b.is_fully_differential


def test_tail_is_cascode():
    ip = [D("mi1", "nmos", g="in1", d="o1", s="t"),
          D("mi2", "nmos", g="in2", d="o2", s="t")]
    # Plain single-device tail: the tail slot is never classified, so this must
    # be detected structurally, not via load_kind (issue #145).
    plain = build_blocks(
        {"input_pair": ip, "tail_current": [D("mt", "nmos", g="net_bias1", d="t", s="0")]}, {})
    assert not plain.tail.is_cascode
    # Cascode tail: top device stacked on the bottom device's drain.
    casc = build_blocks(
        {"input_pair": ip,
         "tail_current": [D("mt_top", "nmos", g="net_bias2", d="t", s="tc"),
                          D("mt_bot", "nmos", g="net_bias1", d="tc", s="0")]}, {})
    assert casc.tail.is_cascode
    # No tail slot at all → nothing to classify (resistor-tail / degenerate case).
    assert build_blocks({"input_pair": ip}, {}).tail is None
