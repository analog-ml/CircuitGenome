"""Tests for the procedural gm/Id geometry pass (no CP-SAT)."""
import pytest

from circuitgenome.sizer.shared.device_model import (
    CURRENT_SOURCE,
    SIGNAL,
    GmIdModel,
    build_device_model,
)
from circuitgenome.sizer.gmid.geometry import assign_geometry_gmid
from circuitgenome.sizer.gmid.intent import TransistorIntent
from circuitgenome.sizer.shared.loader import load_tech
from circuitgenome.synthesizer.models import Device


@pytest.fixture(scope="module")
def tech():
    return load_tech("ptm45")


@pytest.fixture(scope="module")
def model(tech):
    m = build_device_model(tech)
    assert isinstance(m, GmIdModel)
    return m


def _snap_w(tech, w):
    g = tech.width
    return min(max(round(w / g.step) * g.step, g.min), g.max)


# Wrap explicit roles into per-device intents using the registry's role defaults
# (SIGNAL: gm/Id solved, L×2; CURRENT_SOURCE: gm/Id 10, L×4; CASCODE: gm/Id 8, L×3).
_ROLE_DEF = {SIGNAL: (None, 2.0), CURRENT_SOURCE: (10.0, 4.0)}


def _intents(roles):
    return {ref: TransistorIntent(ref=ref, block="test", role=role,
                                  gm_id=_ROLE_DEF[role][0], l_mult=_ROLE_DEF[role][1],
                                  rationale="")
            for ref, role in roles.items()}


def test_mirror_ratio_is_exact(tech, model):
    """Output width tracks the current ratio off the diode-connected reference."""
    ref = Device(ref="mref", type="nmos",
                 terminals={"d": "nbias", "g": "nbias", "s": "0"})  # diode-connected
    out = Device(ref="mout", type="nmos",
                 terminals={"d": "x", "g": "nbias", "s": "0"})       # mirror output
    all_t = {"mref": (ref, "bias_gen"), "mout": (out, "second_stage")}
    slot_t = {"bias_gen": [ref], "second_stage": [out]}
    ids = {"mref": 10e-6, "mout": 25e-6}
    roles = {"mref": CURRENT_SOURCE, "mout": CURRENT_SOURCE}

    sizing, _ = assign_geometry_gmid(model, all_t, slot_t, ids, _intents(roles), {}, tech)

    assert sizing["mout"].l_um == sizing["mref"].l_um            # matched length
    expected = _snap_w(tech, 2.5 * sizing["mref"].w_um)
    assert sizing["mout"].w_um == pytest.approx(expected)
    # current ratio = (W/L)_out / (W/L)_ref ≈ 2.5 within one grid step
    ratio = sizing["mout"].w_um / sizing["mref"].w_um
    assert abs(ratio - 2.5) <= tech.width.step / sizing["mref"].w_um + 1e-9


def test_matched_pair_symmetry(tech, model):
    """Both input-pair devices get the anchor's geometry."""
    d1 = Device(ref="m1", type="pmos", terminals={"d": "o1", "g": "in1", "s": "t"})
    d2 = Device(ref="m2", type="pmos", terminals={"d": "o2", "g": "in2", "s": "t"})
    all_t = {"m1": (d1, "input_pair"), "m2": (d2, "input_pair")}
    slot_t = {"input_pair": [d1, d2]}
    ids = {"m1": 5e-6, "m2": 5e-6}
    roles = {"m1": SIGNAL, "m2": SIGNAL}
    gmt = {"m1": 1e-4, "m2": 1e-4}

    sizing, _ = assign_geometry_gmid(model, all_t, slot_t, ids, _intents(roles), gmt, tech)
    assert sizing["m1"].w_um == sizing["m2"].w_um
    assert sizing["m1"].l_um == sizing["m2"].l_um


def test_geometry_on_grid(tech, model):
    d = Device(ref="m1", type="pmos", terminals={"d": "o1", "g": "in1", "s": "t"})
    all_t = {"m1": (d, "input_pair")}
    slot_t = {"input_pair": [d]}
    sizing, _ = assign_geometry_gmid(
        model, all_t, slot_t, {"m1": 5e-6}, _intents({"m1": SIGNAL}), {"m1": 1e-4}, tech)
    s = sizing["m1"]
    assert s.w_um == pytest.approx(_snap_w(tech, s.w_um))
    assert tech.width.min <= s.w_um <= tech.width.max
    assert tech.length.min <= s.l_um <= tech.length.max


def test_over_ceiling_request_warns_and_clamps(tech, model):
    """A gm/Id beyond the weak-inversion ceiling is clamped with a warning."""
    d = Device(ref="m1", type="nmos", terminals={"d": "o1", "g": "in1", "s": "0"})
    all_t = {"m1": (d, "input_pair")}
    slot_t = {"input_pair": [d]}
    # gm_target/Id = 1e-3 / 5e-6 = 200 /V, far above the table ceiling (~24).
    sizing, warns = assign_geometry_gmid(
        model, all_t, slot_t, {"m1": 5e-6}, _intents({"m1": SIGNAL}), {"m1": 1e-3}, tech)
    assert any("ceiling" in w for w in warns)
    assert sizing["m1"].w_um <= tech.width.max
