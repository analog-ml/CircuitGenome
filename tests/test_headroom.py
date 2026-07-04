"""Tests for the DC headroom / saturation-budget pass (issue #76, cause A)."""
import pytest

from circuitgenome.sizer.shared.device_model import (
    CURRENT_SOURCE,
    SIGNAL,
    GmIdModel,
    Level1Model,
    build_device_model,
)
from circuitgenome.sizer.gmid.bias import _apply_headroom, _tail_gm_id_for_headroom
from circuitgenome.sizer.shared.loader import load_tech
from circuitgenome.sizer.shared.models import SizingSpec, TransistorSizing
from circuitgenome.synthesizer.models import Device


@pytest.fixture(scope="module")
def tech():
    return load_tech("ptm45")


@pytest.fixture(scope="module")
def model(tech):
    m = build_device_model(tech)
    assert isinstance(m, GmIdModel)
    return m


def _size(model, dtype, ids, role, gm_target=None):
    g = model.geometry_for(dtype, ids, role, gm_target)
    return TransistorSizing(
        ref="x", w_um=g.w_um, l_um=g.l_um, ids_a=ids,
        vgs_v=model.vgs(dtype, g.w_um, g.l_um, ids),
        vds_sat_v=model.vds_sat(dtype, g.w_um, g.l_um, ids),
    )


def _scenario(model, vdd, ip_gm_target=125e-6):
    """Build a PMOS-input-pair + PMOS-tail scenario for _apply_headroom."""
    ip = Device(ref="m1_input_pair", type="pmos",
                terminals={"d": "o1", "g": "in1", "s": "net_tail"})
    tail = Device(ref="m1_tail_current", type="pmos",
                  terminals={"d": "net_tail", "g": "nbias", "s": "vdd!"})
    slot = {"input_pair": [ip], "tail_current": [tail]}
    allt = {"m1_input_pair": (ip, "input_pair"),
            "m1_tail_current": (tail, "tail_current")}
    ids = {"m1_input_pair": 10e-6, "m1_tail_current": 20e-6}
    sip = _size(model, "pmos", 10e-6, SIGNAL, ip_gm_target)
    sip = TransistorSizing("m1_input_pair", sip.w_um, sip.l_um, 10e-6, sip.vgs_v, sip.vds_sat_v)
    stl = _size(model, "pmos", 20e-6, CURRENT_SOURCE)
    stl = TransistorSizing("m1_tail_current", stl.w_um, stl.l_um, 20e-6, stl.vgs_v, stl.vds_sat_v)
    sizing = {"m1_input_pair": sip, "m1_tail_current": stl}
    spec = SizingSpec(vdd=vdd, vss=0.0, ibias=20e-6, cl=2e-12)
    return slot, allt, ids, sizing, spec


def test_tail_gm_id_for_headroom_monotone(model):
    # A generous headroom is satisfiable; a near-zero headroom is not.
    assert _tail_gm_id_for_headroom(model, "pmos", 0.18, 0.30) is not None
    assert _tail_gm_id_for_headroom(model, "pmos", 0.18, 0.001) is None


def test_headroom_violation_warns(model, tech):
    # vdd=0.8, mid-rail Vcm, PMOS pair → net_tail near vdd and even a
    # weak-inversion pair cannot make room → warn, sizing untouched.
    slot, allt, ids, sizing, spec = _scenario(model, vdd=0.8)
    sized, warns = _apply_headroom(model, slot, allt, ids, sizing, spec, tech)
    assert warns and "headroom" in warns[0]
    assert sized["m1_input_pair"].w_um == sizing["m1_input_pair"].w_um


def test_headroom_repairs_pair_toward_weak_inversion(model, tech):
    # vdd=1.0: the as-sized pair leaves no tail headroom, but moving the pair
    # toward weak inversion (smaller |Vgs|) makes room (issue #108 follow-up:
    # a gm requirement is a minimum, so the stronger pair is spec-safe).
    slot, allt, ids, sizing, spec = _scenario(model, vdd=1.0)
    sized, warns = _apply_headroom(model, slot, allt, ids, sizing, spec, tech)
    assert warns == []
    assert abs(sized["m1_input_pair"].vgs_v) < abs(sizing["m1_input_pair"].vgs_v)
    assert sized["m1_tail_current"].vds_sat_v < sizing["m1_tail_current"].vds_sat_v


def test_headroom_ok_at_high_supply(model, tech):
    # Plenty of supply → tail keeps its Vdsat → no warning, no resize.
    slot, allt, ids, sizing, spec = _scenario(model, vdd=3.0)
    w0 = sizing["m1_tail_current"].w_um
    sized, warns = _apply_headroom(model, slot, allt, ids, sizing, spec, tech)
    assert warns == []
    assert sized["m1_tail_current"].w_um == w0  # untouched


def test_level1_model_skips_headroom(tech):
    # Headroom pass is gm/Id-only; Level-1 returns no warnings.
    m = Level1Model(load_tech("generic"))
    slot = {"input_pair": [], "tail_current": []}
    spec = SizingSpec(vdd=1.0, vss=0.0, ibias=1e-5, cl=1e-12)
    assert _apply_headroom(m, slot, {}, {}, {}, spec, tech) == ({}, [])
