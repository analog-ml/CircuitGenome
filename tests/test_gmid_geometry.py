"""Tests for the procedural gm/Id geometry pass (no CP-SAT)."""
import pytest

from circuitgenome.sizer.shared.device_model import (
    CURRENT_SOURCE,
    SIGNAL,
    GmIdModel,
    build_device_model,
)
from circuitgenome.sizer.gmid.geometry import assign_geometry_gmid
from circuitgenome.sizer.gmid.intent import (
    TransistorIntent,
    resolve_transistor_intents,
)
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

    sizing, _, _ = assign_geometry_gmid(model, all_t, slot_t, ids, _intents(roles), {}, tech)

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

    sizing, _, _ = assign_geometry_gmid(model, all_t, slot_t, ids, _intents(roles), gmt, tech)
    assert sizing["m1"].w_um == sizing["m2"].w_um
    assert sizing["m1"].l_um == sizing["m2"].l_um


def test_geometry_on_grid(tech, model):
    d = Device(ref="m1", type="pmos", terminals={"d": "o1", "g": "in1", "s": "t"})
    all_t = {"m1": (d, "input_pair")}
    slot_t = {"input_pair": [d]}
    sizing, _, _ = assign_geometry_gmid(
        model, all_t, slot_t, {"m1": 5e-6}, _intents({"m1": SIGNAL}), {"m1": 1e-4}, tech)
    s = sizing["m1"]
    assert s.w_um == pytest.approx(_snap_w(tech, s.w_um))
    assert tech.width.min <= s.w_um <= tech.width.max
    assert tech.length.min <= s.l_um <= tech.length.max


def _cs_load_circuit():
    """Single-ended first stage: mirrored current-source loads + a MOSFET tail."""
    mref = Device(ref="mref", type="nmos",
                  terminals={"d": "nbias", "g": "nbias", "s": "0"})
    ml1 = Device(ref="ml1", type="nmos",
                 terminals={"d": "o1", "g": "nbias", "s": "0"})
    ml2 = Device(ref="ml2", type="nmos",
                 terminals={"d": "o2", "g": "nbias", "s": "0"})
    mt = Device(ref="mt", type="pmos",
                terminals={"d": "t", "g": "pbias", "s": "vdd!"})
    all_t = {"mref": (mref, "bias_gen"), "ml1": (ml1, "load"),
             "ml2": (ml2, "load"), "mt": (mt, "tail_current")}
    slot_t = {"bias_gen": [mref], "load": [ml1, ml2], "tail_current": [mt]}
    # Currents large enough that a 5% width margin is representable on the grid.
    ids = {"mref": 200e-6, "ml1": 100e-6, "ml2": 100e-6, "mt": 200e-6}
    roles = {r: CURRENT_SOURCE for r in ids}
    return all_t, slot_t, ids, roles


def test_current_source_load_gets_margin(tech, model):
    """A single-ended plain current-source load runs _LOAD_CS_MARGIN strong.

    The exact mirror ratio would leave the load-vs-tail current balance on a
    knife edge (no feedback fixes the fold node); the deliberate margin makes
    the node settle toward the load's supply rail.
    """
    all_t, slot_t, ids, roles = _cs_load_circuit()
    sizing, _, _ = assign_geometry_gmid(model, all_t, slot_t, ids, _intents(roles), {}, tech)
    exact = _snap_w(tech, 0.5 * sizing["mref"].w_um)
    expected = _snap_w(tech, 1.05 * exact)
    assert expected > exact  # margin representable on this width grid
    assert sizing["ml1"].w_um == pytest.approx(expected)
    assert sizing["ml2"].w_um == pytest.approx(expected)


def test_no_margin_without_a_tail(tech, model):
    """Without a tail current the balance is not knife-edge: exact ratio kept."""
    all_t, slot_t, ids, roles = _cs_load_circuit()
    del all_t["mt"], ids["mt"], roles["mt"], slot_t["tail_current"]
    sizing, _, _ = assign_geometry_gmid(model, all_t, slot_t, ids, _intents(roles), {}, tech)
    exact = _snap_w(tech, 0.5 * sizing["mref"].w_um)
    assert sizing["ml1"].w_um == pytest.approx(exact)


def _fd_follower_pair():
    """Buffered FD output stage: a common_drain follower plus its bias current
    source on each of the p and n paths.

    All four devices share polarity (a common_drain follower and its current
    source are the same type) and carry the same series IDS — the exact shape
    that a naive ``_FD_PAIRS`` entry would mis-equalize (issue #175).
    """
    def follower(ref, side):
        return Device(ref=ref, type="pmos",
                      terminals={"d": "gnd!", "g": f"net_ampout_{side}",
                                 "s": f"out_{side}", "b": f"out_{side}"})

    def csource(ref, side):
        return Device(ref=ref, type="pmos",
                      terminals={"d": f"out_{side}", "g": "net_biasp",
                                 "s": "vdd!", "b": "vdd!"})

    fp, cp = follower("mfp", "p"), csource("mcp", "p")
    fn, cn = follower("mfn", "n"), csource("mcn", "n")
    all_t = {"mfp": (fp, "output_stage_p"), "mcp": (cp, "output_stage_p"),
             "mfn": (fn, "output_stage_n"), "mcn": (cn, "output_stage_n")}
    slot_t = {"output_stage_p": [fp, cp], "output_stage_n": [fn, cn]}
    ids = {r: 40e-6 for r in all_t}   # series stack: one branch current for both
    return all_t, slot_t, ids


def test_fd_follower_pair_matches_per_role(tech, model):
    """FD follower buffers match p↔n from identical intent, not via _FD_PAIRS.

    Guards issue #175: ``output_stage_p``/``_n`` are deliberately absent from
    ``_FD_PAIRS``.  Each slot's follower and bias current source share polarity
    and IDS, so the type-only ``_FD_PAIRS`` equalizer would wrongly fuse them.
    The correct outcome, asserted here: p == n *per role*, and the follower is
    sized differently from its current source.
    """
    all_t, slot_t, ids = _fd_follower_pair()
    intents = resolve_transistor_intents(all_t, cascode_refs=set())
    # the registry splits the two same-polarity devices by role
    assert intents["mfp"].block == "output_stage"   # source follower
    assert intents["mcp"].block == "stage_load"      # bias current source

    sizing, _, _ = assign_geometry_gmid(model, all_t, slot_t, ids, intents, {}, tech)

    # p and n match device-for-device (the invariant #172 relies on)
    assert (sizing["mfp"].w_um, sizing["mfp"].l_um) == (sizing["mfn"].w_um, sizing["mfn"].l_um)
    assert (sizing["mcp"].w_um, sizing["mcp"].l_um) == (sizing["mcn"].w_um, sizing["mcn"].l_um)
    # follower and its bias source are distinct — a naive _FD_PAIRS entry would fuse them
    assert (sizing["mfp"].w_um, sizing["mfp"].l_um) != (sizing["mcp"].w_um, sizing["mcp"].l_um)


def test_over_ceiling_request_warns_and_clamps(tech, model):
    """A gm/Id beyond the weak-inversion ceiling is clamped with a warning."""
    d = Device(ref="m1", type="nmos", terminals={"d": "o1", "g": "in1", "s": "0"})
    all_t = {"m1": (d, "input_pair")}
    slot_t = {"input_pair": [d]}
    # gm_target/Id = 1e-3 / 5e-6 = 200 /V, far above the table ceiling (~24).
    sizing, warns, _ = assign_geometry_gmid(
        model, all_t, slot_t, {"m1": 5e-6}, _intents({"m1": SIGNAL}), {"m1": 1e-3}, tech)
    assert any("ceiling" in w for w in warns)
    assert sizing["m1"].w_um <= tech.width.max

# --- swing-derived Vdsat budgets (vod_max_map, issue #126) -------------------

def _output_stage_device():
    d = Device(ref="mp1", type="pmos",
               terminals={"d": "out", "g": "mid", "s": "vdd!"})
    all_t = {"mp1": (d, "second_stage")}
    slot_t = {"second_stage": [d]}
    return all_t, slot_t


def test_swing_budget_floors_output_gm_id(model, tech):
    """An output-stage device's Vdsat is pushed to half its swing budget."""
    all_t, slot_t = _output_stage_device()
    ids, gmt = {"mp1": 50e-6}, {"mp1": 3e-4}   # solved gm/Id = 6 (strong)
    base, _, _ = assign_geometry_gmid(
        model, all_t, slot_t, ids, _intents({"mp1": SIGNAL}), gmt, tech)
    sized, warns, feasible = assign_geometry_gmid(
        model, all_t, slot_t, ids, _intents({"mp1": SIGNAL}), gmt, tech,
        vod_max_map={"mp1": 0.3})
    assert feasible and not warns
    assert base["mp1"].vds_sat_v > 0.15          # solved point missed the margin
    assert sized["mp1"].vds_sat_v <= 0.15 + 5e-3  # floored to ~budget/2
    # spec-safe: the realized gm can only grow past the requirement
    gm = model.gm("pmos", sized["mp1"].w_um, sized["mp1"].l_um, 50e-6)
    assert gm >= 3e-4 * 0.99


def test_swing_budget_infeasible_flags(model, tech):
    """A budget below even weak-inversion Vdsat fails sizing explicitly."""
    all_t, slot_t = _output_stage_device()
    _, warns, feasible = assign_geometry_gmid(
        model, all_t, slot_t, {"mp1": 50e-6}, _intents({"mp1": SIGNAL}),
        {"mp1": 3e-4}, tech, vod_max_map={"mp1": 0.02})
    assert not feasible
    assert any("swing" in w for w in warns)


def test_swing_budget_skips_mirror_outputs(model, tech):
    """A mirror-tied output device keeps the diode's inversion level."""
    mref = Device(ref="mref", type="nmos",
                  terminals={"d": "nbias", "g": "nbias", "s": "0"})
    mout = Device(ref="mout", type="nmos",
                  terminals={"d": "out", "g": "nbias", "s": "0"})
    all_t = {"mref": (mref, "bias_gen"), "mout": (mout, "second_stage")}
    slot_t = {"bias_gen": [mref], "second_stage": [mout]}
    ids = {"mref": 10e-6, "mout": 25e-6}
    roles = {"mref": CURRENT_SOURCE, "mout": CURRENT_SOURCE}
    base, _, _ = assign_geometry_gmid(
        model, all_t, slot_t, ids, _intents(roles), {}, tech)
    sized, _, feasible = assign_geometry_gmid(
        model, all_t, slot_t, ids, _intents(roles), {}, tech,
        vod_max_map={"mout": 0.3})
    assert feasible
    assert sized["mout"].w_um == base["mout"].w_um   # ratio to the diode kept


def test_swing_budget_spares_driver_under_cascode_load(model, tech):
    """With a cascode first-stage load, the driver's V_GS is the stage
    interface pin — the swing floor must not move it."""
    from circuitgenome.sizer.shared.device_model import CASCODE

    d = Device(ref="mp1", type="pmos",
               terminals={"d": "out", "g": "mid", "s": "vdd!"})
    lc = Device(ref="ml_casc", type="pmos",
                terminals={"d": "mid", "g": "bc", "s": "lx"})
    all_t = {"mp1": (d, "second_stage"), "ml_casc": (lc, "load")}
    slot_t = {"second_stage": [d], "load": [lc]}
    intents = _intents({"mp1": SIGNAL})
    intents["ml_casc"] = TransistorIntent(ref="ml_casc", block="cascode",
                                          role=CASCODE, gm_id=8.0, l_mult=3.0,
                                          rationale="")
    ids = {"mp1": 50e-6, "ml_casc": 10e-6}
    base, _, _ = assign_geometry_gmid(
        model, all_t, slot_t, ids, intents, {"mp1": 3e-4}, tech)
    sized, _, feasible = assign_geometry_gmid(
        model, all_t, slot_t, ids, intents, {"mp1": 3e-4}, tech,
        vod_max_map={"mp1": 0.3})
    assert feasible
    assert sized["mp1"].vgs_v == base["mp1"].vgs_v   # pin level untouched
