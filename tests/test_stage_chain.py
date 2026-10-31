"""Tests for the stage chain and the metric algebra over it.

These sit between the single-formula tests in ``test_sizer.py`` and the
end-to-end ``size_circuit`` runs: a chain is a handful of floats, so metric
behaviour can be pinned without synthesizing and recognizing a circuit first.
"""
import math

import pytest

from circuitgenome.sizer import SizingSpec
from circuitgenome.sizer.models import TransistorSizing
from circuitgenome.sizer.physics import equations as eq
from circuitgenome.sizer.physics.circuit_view import CircuitView
from circuitgenome.sizer.physics.metrics import evaluate_metrics
from circuitgenome.sizer.physics.rnmc import RNMC
from circuitgenome.synthesizer.models import Device
from circuitgenome.sizer.physics.stage_chain import (
    Stage,
    StageChain,
    _CMFB_SENSE_SWING_TOL_V,
    _FOLLOWER_SWING_TOL_V,
    check_cmfb_sense_swing,
    check_follower_swing,
    cmfb_sense_range,
    output_swing_headroom,
    _se_rejection_db,
    _source_degeneration_r,
    _telescopic_cascode_load,
    _tail_current_net,
)

INF = float("inf")


def _spec(**kw):
    base = dict(vdd=5.0, vss=0.0, ibias=10e-6, cl=20e-12)
    base.update(kw)
    return SizingSpec(**base)


def _chain(*stages, **kw):
    """Each stage is ``(gm, rout)`` or ``(gm, rout, ids)``."""
    return StageChain(stages=tuple(Stage(*s) for s in stages), **kw)


# --------------------------------------------------------------------------- #
# Gain: the per-stage cascade product
# --------------------------------------------------------------------------- #
def test_two_stage_gain_is_the_cascade_product():
    chain = _chain((1e-3, 1e6), (2e-3, 5e5))
    m, _ = evaluate_metrics(chain, _spec())
    assert m["gain_db"] == pytest.approx(eq.open_loop_gain_db([1e-3 * 1e6, 2e-3 * 5e5]))


def test_three_stage_gain_includes_every_stage():
    chain = _chain((1e-3, 1e6), (2e-3, 5e5), (3e-3, 2e5))
    m, _ = evaluate_metrics(chain, _spec())
    expected = eq.open_loop_gain_db([1e-3 * 1e6, 2e-3 * 5e5, 3e-3 * 2e5])
    assert m["gain_db"] == pytest.approx(expected)


def test_k_fs_halves_the_first_stage_only():
    """A single-ended tap without a mirror load delivers gm1·Rout1/2."""
    full = evaluate_metrics(_chain((1e-3, 1e6), (2e-3, 5e5)), _spec())[0]
    half = evaluate_metrics(_chain((1e-3, 1e6), (2e-3, 5e5), k_fs=0.5), _spec())[0]
    # Exactly one factor of two, on the first stage: -6.02 dB overall.
    assert half["gain_db"] == pytest.approx(full["gain_db"] - 20 * math.log10(2.0))


def test_gain_truncates_at_the_first_infinite_rout():
    """A stage with no finite output resistance ends the cascade product."""
    chain = _chain((1e-3, 1e6), (2e-3, INF), (3e-3, 2e5))
    m, _ = evaluate_metrics(chain, _spec())
    assert m["gain_db"] == pytest.approx(eq.open_loop_gain_db([1e-3 * 1e6]))


def test_no_gain_reported_when_the_first_stage_is_open():
    m, _ = evaluate_metrics(_chain((1e-3, INF), (2e-3, 5e5)), _spec())
    assert "gain_db" not in m


# --------------------------------------------------------------------------- #
# GBW / PM / slew: what the Miller cap gates
# --------------------------------------------------------------------------- #
def test_gbw_and_slew_need_the_miller_cap():
    bare = evaluate_metrics(_chain((1e-3, 1e6), (2e-3, 5e5)), _spec())[0]
    assert "gbw_hz" not in bare and "slew_rate_vps" not in bare

    comp = evaluate_metrics(_chain((1e-3, 1e6, 10e-6), (2e-3, 5e5, 1e-3),
                                   cc_pf=2.0), _spec())[0]
    assert comp["gbw_hz"] == pytest.approx(
        eq.two_stage_loop(1e-3, 2e-3, 2e-12, 20e-12, g1=1e-6, g2=2e-6)[0])
    assert comp["slew_rate_vps"] == pytest.approx(eq.slew_rate_vps(10e-6, 2e-12))


def _two_stage(gm2, *, resistor, buffer=None):
    """A 1 mA/V pair over a ``gm2`` second stage, Cc = 2 pF into 20 pF."""
    return _chain((1e-3, 1e6), (gm2, 5e5), cc_pf=2.0, output_buffer=buffer,
                  comp_resistors=((resistor, False),))


def test_a_bare_miller_cap_pulls_the_crossing_under_its_output_pole():
    """Issue #274: at a ~55° margin the output pole puts the 0 dB crossing
    ~14 % under gm1/(2π·Cc) -- SPICE measured −13 % and ~51°."""
    m, _ = evaluate_metrics(_two_stage(14e-3, resistor=False), _spec())
    assert 45 < m["phase_margin_deg"] < 60
    assert 0.8 < m["gbw_hz"] / eq.unity_gain_bw(1e-3, 2e-12) < 0.9


def test_a_nulling_resistor_cancels_the_output_pole():
    """Issue #274: R = (Cc+CL)/(gm2·Cc), as size_resistors sizes it, puts the
    zero on the output pole -- SPICE measured ~89° and no pull on GBW."""
    m, _ = evaluate_metrics(_two_stage(14e-3, resistor=True), _spec())
    assert m["phase_margin_deg"] > 85
    assert m["gbw_hz"] / eq.unity_gain_bw(1e-3, 2e-12) == pytest.approx(1.0, abs=0.02)


def test_gbw_scales_with_the_output_followers_gain():
    """Issue #274: a source follower passes gm/(gm + gmb + g_out) of its
    gate's swing, and the 0 dB crossing moves down with it."""
    buffer = (0.1, 10e-15, 12.5e-3)              # gain 1/1.125, pole 11x crossover
    m, _ = evaluate_metrics(_two_stage(14e-3, resistor=False, buffer=buffer), _spec())
    assert m["gbw_hz"] / eq.unity_gain_bw(1e-3, 2e-12) == pytest.approx(1 / 1.125, abs=0.02)


def test_nested_miller_recovers_the_textbook_crossing_with_fast_stages():
    """Stages far faster than the crossover leave gm1/(2π·Cc1) and ~90°."""
    gbw, pm = eq.nested_miller_loop(1e-3, 1.0, 10.0, 2e-12, 1e-12, 20e-12,
                                    g1=1e-6, g2=1e-4, g3=1e-3)
    assert gbw == pytest.approx(eq.unity_gain_bw(1e-3, 2e-12), rel=0.02)
    assert pm > 85


def test_a_return_toward_0_db_counts_as_a_crossing():
    """Issue #274: GF180 NMC #121 crosses 0 dB at 9 MHz with ~98° left, but
    its lightly damped inner pair lifts the gain back to -0.3 dB near
    27 MHz.  SPICE puts that peak at +0.04 dB -- a second crossing, 47° --
    and the model cannot tell the two apart, so the return sets the margin."""
    kw = dict(c1_f=5.31e-15, c2_f=103.5e-15, g1=6.98e-9, g2=2.42e-6, g3=5.4e-6,
              mirror_pole_hz=952.8e6)
    args = (6e-5, 2.4e-4, 8.4e-4, 1.25e-12, 1.008e-12, 5e-12)
    gbw, pm = eq.nested_miller_loop(*args, comp_r=((5952.0, 0.0),), **kw)
    assert gbw == pytest.approx(9.25e6, rel=0.02)
    assert 40 < pm < 50
    # A nulling resistor of 1/gm3 leaves no such peak: the first crossing sets it.
    assert eq.nested_miller_loop(*args, comp_r=((1 / 8.4e-4, 0.0),), **kw)[1] > 75


def test_a_mixed_fd_amplifier_averages_its_halves():
    """Issue #274: a fully-differential amplifier with a bare cap on one half
    and a nulling resistor on the other reads between the two (51° and 89°
    halves measure ~65° in SPICE): with no CMFB to couple them, the
    differential output averages its halves, where the worse half alone
    would read 51°."""
    def pm(halves):
        chain = _chain((1e-3, 1e6), (14e-3, 5e5), cc_pf=2.0, comp_resistors=halves)
        return evaluate_metrics(chain, _spec())[0]["phase_margin_deg"]
    bare, nulled = pm(((False, False),)), pm(((True, False),))
    assert bare + 5 < pm(((False, False), (True, False))) < nulled - 5


def test_the_cmfb_couples_mismatched_halves_only():
    """Issue #274: the CMFB feeds the outputs' common mode back into both
    first-stage nodes.  Matched halves have none to feed back; mismatched
    ones are pulled from their average toward the halves' harmonic mean,
    still above the worse half."""
    args = (1e-3, 14e-3, 2e-12, 20e-12)
    kw = dict(g1=1e-6, g2=2e-6)
    r = eq.compensation_resistor_ohm(2e-12, 20e-12, 14e-3)
    strong = (10e-3, None)
    assert eq.two_stage_loop(*args, r_ohm=(0.0, 0.0), cmfb=strong, **kw) == \
        pytest.approx(eq.two_stage_loop(*args, **kw))
    free = eq.two_stage_loop(*args, r_ohm=(0.0, r), **kw)[1]
    worse = eq.two_stage_loop(*args, **kw)[1]
    for cmfb in ((1e-3, None), strong):
        assert worse + 5 < eq.two_stage_loop(*args, r_ohm=(0.0, r), cmfb=cmfb, **kw)[1] < free - 3


def test_two_stage_replan_raises_gm2_for_the_phase_margin():
    """Issue #274: the closed-form PM rule ignores the bare Miller cap's
    right-half-plane zero; the re-plan finds the gm2 the full model needs."""
    from circuitgenome.sizer.physics.metrics import two_stage_replan
    chain = _chain((1e-3, 1e6), (8e-3, 5e5), cc_pf=2.0, node_caps_f=(0.0, 0.0))
    spec = _spec(phase_margin_min_deg=60)
    assert evaluate_metrics(chain, spec)[0]["phase_margin_deg"] < 60
    gm1, gm2 = two_stage_replan(chain, spec, lambda gm: 0.0, 1.0, 1.0)
    assert gm1 is None and gm2 > 8e-3
    raised = _chain((1e-3, 1e6), (gm2, 5e5), cc_pf=2.0, node_caps_f=(0.0, 0.0))
    assert evaluate_metrics(raised, spec)[0]["phase_margin_deg"] == pytest.approx(60.5, abs=0.05)


def test_two_stage_replan_raises_gm1_for_gbw_first():
    """GBW is met first, through gm1; the phase margin then through gm2,
    which only lifts GBW further."""
    from circuitgenome.sizer.physics.metrics import two_stage_replan
    chain = _chain((1e-3, 1e6), (14e-3, 5e5), cc_pf=2.0, node_caps_f=(0.0, 0.0))
    gbw = evaluate_metrics(chain, _spec())[0]["gbw_hz"]
    spec = _spec(gbw_min_hz=1.2 * gbw, phase_margin_min_deg=50)
    gm1, gm2 = two_stage_replan(chain, spec, lambda gm: 0.0, 1.0, 1.0)
    raised = _chain((gm1, 1e6), (gm2 or 14e-3, 5e5), cc_pf=2.0, node_caps_f=(0.0, 0.0))
    m = evaluate_metrics(raised, spec)[0]
    assert m["gbw_hz"] >= spec.gbw_min_hz and m["phase_margin_deg"] >= 50


def test_two_stage_replan_leaves_an_unreachable_spec():
    """No gm2 under the ceiling reaches the margin: nothing to re-size."""
    from circuitgenome.sizer.physics.metrics import two_stage_replan
    chain = _chain((1e-3, 1e6), (8e-3, 5e5), cc_pf=2.0, node_caps_f=(0.0, 0.0))
    assert two_stage_replan(chain, _spec(phase_margin_min_deg=89), lambda gm: 0.0,
                            1.0, 9e-3) == (None, None)


class _FollowerModel:
    """gm/gds/cgs constants; ``gmb`` is ``0.13·gm`` at any reverse body bias
    and records the ``vsb`` it was read at."""

    def __init__(self):
        self.vsb = None

    def gm(self, dtype, w, l, ids):
        return 1e-3

    def gds(self, dtype, w, l, ids, vds=None):
        return 1e-5

    def cgs(self, dtype, w, l):
        return 10e-15

    def gmb(self, dtype, w, l, ids, vsb):
        self.vsb = vsb
        return 0.13e-3 if vsb > 0 else 0.0


def _follower_slot(bulk):
    """An NMOS follower (gate on the amplifier node) over its bias sink."""
    fol = Device(ref="m1_os", type="nmos",
                 terminals={"d": "vdd!", "g": "net_amp", "s": "out", "b": bulk})
    sink = Device(ref="m2_os", type="nmos",
                  terminals={"d": "out", "g": "net_bias6", "s": "gnd!", "b": "gnd!"})
    sizing = {d.ref: TransistorSizing(ref=d.ref, w_um=1.0, l_um=1.0, ids_a=1e-5,
                                      vgs_v=0.8, vds_sat_v=0.2) for d in (fol, sink)}
    return {"output_stage": [fol, sink]}, sizing


def test_output_buffer_counts_the_followers_body_effect():
    """Issue #274: a follower whose bulk sits on a rail loses gmb at its
    output, read at the output's quiescent level spec.vcm."""
    from circuitgenome.sizer.physics.stage_chain import _output_buffer
    model = _FollowerModel()
    slots, sizing = _follower_slot(bulk="gnd!")
    gm, cgs, g_out = _output_buffer(slots, model, sizing, _spec(vdd=3.3))
    assert (gm, cgs) == (1e-3, 10e-15)
    assert g_out == pytest.approx(2 * 1e-5 + 0.13e-3)
    assert model.vsb == pytest.approx(1.65)


def test_output_buffer_has_no_body_effect_with_bulk_on_source():
    from circuitgenome.sizer.physics.stage_chain import _output_buffer
    model = _FollowerModel()
    slots, sizing = _follower_slot(bulk="out")
    assert _output_buffer(slots, model, sizing, _spec())[2] == pytest.approx(2e-5)
    assert model.vsb is None


# --------------------------------------------------------------------------- #
# Slew rate: the slowest current-limited node
# --------------------------------------------------------------------------- #
_CL = 5e-12


def _slew(chain):
    return evaluate_metrics(chain, _spec(cl=_CL))[0]["slew_rate_vps"]


def test_two_stage_slew_is_limited_by_the_second_stage_driving_cl_and_cc():
    """The second stage's quiescent current charges CL plus the Miller cap."""
    chain = _chain((1e-3, 1e6, 20e-6), (2e-3, 5e5, 40e-6), cc_pf=1.25)
    # input stage 20 µA / 1.25 pF = 16 V/µs; second stage 40 µA / 6.25 pF = 6.4
    assert _slew(chain) == pytest.approx(40e-6 / (1.25e-12 + _CL))


def test_two_stage_slew_is_limited_by_the_input_stage_charging_cc():
    chain = _chain((1e-3, 1e6, 20e-6), (2e-3, 5e5, 1e-3), cc_pf=1.25)
    assert _slew(chain) == pytest.approx(20e-6 / 1.25e-12)


def test_k_slew_scales_the_input_stage_limit():
    """Without a mirror (or per side of an FD pair) only half the tail swings."""
    chain = _chain((1e-3, 1e6, 20e-6), (2e-3, 5e5, 1e-3), cc_pf=1.25, k_slew=0.5)
    assert _slew(chain) == pytest.approx(10e-6 / 1.25e-12)


def test_buffered_slew_is_limited_by_the_follower_driving_cl():
    """Behind a follower, CL is the follower's to charge, not the gain stage's."""
    chain = _chain((1e-3, 1e6, 20e-6), (2e-3, 5e5, 40e-6), cc_pf=1.25,
                   buffer_ids=20e-6)
    # input 16 V/µs, second stage 40 µA / 1.25 pF = 32, follower 20 µA / 5 pF = 4
    assert _slew(chain) == pytest.approx(20e-6 / _CL)


def test_buffered_gain_stage_drives_only_the_miller_cap():
    chain = _chain((1e-3, 1e6, 1e-3), (2e-3, 5e5, 10e-6), cc_pf=1.25,
                   buffer_ids=1e-3)
    assert _slew(chain) == pytest.approx(10e-6 / 1.25e-12)


def test_nmc_slew_includes_the_inner_miller_loop():
    """Nested Miller: the second stage alone charges the inner cap Cc2."""
    chain = _chain((1e-3, 1e6, 1e-3), (2e-3, 5e5, 10e-6), (3e-3, 2e5, 1e-3),
                   cc_pf=1.25, cc2_pf=1.0)
    assert _slew(chain) == pytest.approx(10e-6 / 1.0e-12)


def test_nmc_output_node_carries_both_miller_caps():
    chain = _chain((1e-3, 1e6, 1e-3), (2e-3, 5e5, 1e-3), (3e-3, 2e5, 40e-6),
                   cc_pf=1.25, cc2_pf=1.0)
    assert _slew(chain) == pytest.approx(40e-6 / (_CL + 1.25e-12 + 1.0e-12))


def test_rnmc_output_node_carries_only_the_outer_cap():
    """Reversed nested Miller hangs Cc2 between stages 1 and 2, off the output,
    and the second stage then drives no Miller cap of its own."""
    chain = _chain((1e-3, 1e6, 1e-3), (2e-3, 5e5, 10e-6), (3e-3, 2e5, 40e-6),
                   cc_pf=1.25, cc2_pf=1.0, compensation_scheme=RNMC)
    assert _slew(chain) == pytest.approx(40e-6 / (_CL + 1.25e-12))


def test_k_fs_scales_gbw_but_not_cmrr():
    """k_fs is the loop transconductance; CMRR sees the raw device gm."""
    kw = dict(cc_pf=2.0, gd_tail=1e-7)
    full = evaluate_metrics(_chain((1e-3, 1e6), (2e-3, 5e5), **kw), _spec())[0]
    half = evaluate_metrics(_chain((1e-3, 1e6), (2e-3, 5e5), k_fs=0.5, **kw), _spec())[0]
    assert half["gbw_hz"] == pytest.approx(
        eq.two_stage_loop(0.5e-3, 2e-3, 2e-12, 20e-12, g1=1e-6, g2=2e-6)[0])
    assert half["cmrr_db"] == pytest.approx(full["cmrr_db"])


def test_three_stage_uses_the_nested_miller_model():
    chain = _chain((1e-3, 1e6), (2e-3, 5e5), (3e-3, 2e5), cc_pf=2.0, cc2_pf=0.5)
    m, _ = evaluate_metrics(chain, _spec())
    gbw, pm = eq.nested_miller_loop(1e-3, 2e-3, 3e-3, 2e-12, 0.5e-12, 20e-12,
                                    g1=1e-6, g2=2e-6, g3=5e-6)
    assert (m["gbw_hz"], m["phase_margin_deg"]) == (pytest.approx(gbw), pytest.approx(pm))


# --------------------------------------------------------------------------- #
# Resolving the tail node through degeneration resistors (issue #224)
# --------------------------------------------------------------------------- #
def _res(ref, t1, t2):
    return Device(ref=ref, type="resistor", terminals={"t1": t1, "t2": t2})


def test_plain_pair_tail_node_is_the_pair_source():
    """No degeneration resistors -- the source net *is* the tail node."""
    assert _tail_current_net("net_tail", []) == "net_tail"


def test_degenerated_pair_tail_node_hops_the_resistor():
    """r1 bridges the pair source to the shared tail the mirror drains onto."""
    rs = [_res("r1", "s1", "net_tail"), _res("r2", "s2", "net_tail")]
    assert _tail_current_net("s1", rs) == "net_tail"
    assert _tail_current_net("s2", rs) == "net_tail"


def test_tail_node_hop_is_terminal_order_independent():
    """t1/t2 order is a netlist detail, not a direction."""
    assert _tail_current_net("s1", [_res("r1", "net_tail", "s1")]) == "net_tail"


def test_tail_node_ignores_resistors_not_on_the_pair_source():
    """A degeneration resistor on the *other* leg must not redirect this one."""
    assert _tail_current_net("s1", [_res("r2", "s2", "net_tail")]) == "s1"


def test_tail_node_of_a_pairless_chain_is_none():
    assert _tail_current_net(None, []) is None


# The load-compensated single-stage path (issue #221)
# --------------------------------------------------------------------------- #
def _one_stage(**kw):
    """A single-stage chain with a placeable mirror pole well past the GBW."""
    base = dict(mirror_pole_hz=1e9, gd_output_load=1e-7, gd_tail=1e-7,
                supply_currents=(10e-6,))
    ids = kw.pop("ids", 10e-6)
    base.update(kw)
    return _chain((1e-3, 1e6, ids), **base)


def test_single_stage_reports_every_performance_metric():
    """A one-stage OTA is load-compensated, not unmodellable (issue #221)."""
    m, _ = evaluate_metrics(_one_stage(), _spec())
    assert set(m) >= {"gain_db", "gbw_hz", "phase_margin_deg",
                      "slew_rate_vps", "cmrr_db", "psrr_db"}


def test_single_stage_gbw_and_slew_are_set_by_cl_not_cc():
    """With no Miller cap, CL is what gm1 drives and what the tail charges."""
    m, _ = evaluate_metrics(_one_stage(), _spec(cl=20e-12))
    assert m["gbw_hz"] == pytest.approx(eq.unity_gain_bw(1e-3, 20e-12))
    assert m["slew_rate_vps"] == pytest.approx(eq.slew_rate_vps(10e-6, 20e-12))


def test_single_stage_gbw_and_slew_scale_inversely_with_cl():
    small = evaluate_metrics(_one_stage(), _spec(cl=5e-12))[0]
    large = evaluate_metrics(_one_stage(), _spec(cl=20e-12))[0]
    assert large["gbw_hz"] == pytest.approx(small["gbw_hz"] / 4.0)
    assert large["slew_rate_vps"] == pytest.approx(small["slew_rate_vps"] / 4.0)


def test_single_stage_slew_scales_with_the_tail_current():
    lo = evaluate_metrics(_one_stage(ids=10e-6), _spec())[0]
    hi = evaluate_metrics(_one_stage(ids=20e-6), _spec())[0]
    assert hi["slew_rate_vps"] == pytest.approx(2.0 * lo["slew_rate_vps"])


def test_single_stage_k_slew_halves_the_tail_current_into_cl():
    m = evaluate_metrics(_one_stage(k_slew=0.5), _spec(cl=20e-12))[0]
    assert m["slew_rate_vps"] == pytest.approx(eq.slew_rate_vps(5e-6, 20e-12))


def test_single_stage_phase_margin_comes_from_the_mirror_pole():
    m, _ = evaluate_metrics(_one_stage(mirror_pole_hz=1e8), _spec())
    assert m["phase_margin_deg"] == pytest.approx(
        eq.phase_margin_single_stage_deg(eq.unity_gain_bw(1e-3, 20e-12), 1e8))


def test_single_stage_phase_margin_falls_as_the_mirror_pole_closes_in():
    """A mirror pole near the GBW is exactly what this metric exists to catch."""
    far = evaluate_metrics(_one_stage(mirror_pole_hz=1e9), _spec())[0]
    near = evaluate_metrics(_one_stage(mirror_pole_hz=1e7), _spec())[0]
    assert far["phase_margin_deg"] > near["phase_margin_deg"]
    assert far["phase_margin_deg"] < 90.0


def test_single_stage_phase_margin_withheld_without_a_mirror_pole():
    """A resistor-loaded stage has no mirror node — omitted, not faked at 90."""
    m, _ = evaluate_metrics(_one_stage(mirror_pole_hz=None), _spec())
    assert "phase_margin_deg" not in m
    assert "gbw_hz" in m and "slew_rate_vps" in m


def test_single_stage_psrr_uses_the_input_pair_against_its_own_load():
    m, _ = evaluate_metrics(_one_stage(gd_output_load=1e-7), _spec())
    assert m["psrr_db"] == pytest.approx(eq.psrr_db_approx(1e-3, 1e-7))


def test_single_stage_psrr_takes_the_k_fs_single_ended_penalty():
    """PSRR rides the same loop transconductance the gain does."""
    full = evaluate_metrics(_one_stage(), _spec())[0]
    half = evaluate_metrics(_one_stage(k_fs=0.5), _spec())[0]
    assert half["psrr_db"] == pytest.approx(full["psrr_db"] - 20 * math.log10(2.0))


def test_a_miller_cap_still_wins_when_a_mirror_pole_is_present():
    """Multi-stage chains keep the Miller model: the single-stage mirror pole
    does not enter it (issue #221)."""
    chain = _chain((1e-3, 1e6), (2e-3, 5e5), cc_pf=2.0, mirror_pole_hz=1e8)
    m, _ = evaluate_metrics(chain, _spec())
    gbw, pm = eq.two_stage_loop(1e-3, 2e-3, 2e-12, 20e-12, g1=1e-6, g2=2e-6)
    assert m["gbw_hz"] == pytest.approx(gbw)
    assert m["phase_margin_deg"] == pytest.approx(pm)


# --------------------------------------------------------------------------- #
# The degeneration resistance each pair leg sees (issue #226)
# --------------------------------------------------------------------------- #
def _mos(ref, src):
    return Device(ref=ref, type="pmos", terminals={"d": "o", "g": "in", "s": src})


def test_each_leg_gets_its_own_degeneration_resistance():
    """The tail is a virtual ground for the differential half-circuit, so each
    leg sees its own R -- not the 2R across both."""
    devs = [_mos("m1", "s1"), _mos("m2", "s2")]
    rs = [_res("r1", "s1", "net_tail"), _res("r2", "net_tail", "s2")]
    assert _source_degeneration_r(devs, rs, {"r1": 500.0, "r2": 500.0}) == {
        "s1": 500.0, "s2": 500.0}


def test_unsized_degeneration_resistors_boost_nothing():
    """The synthesizer's 1 kΩ placeholder is not a degeneration value.

    `size_resistors` leaves r1/r2 alone when the intent asks for no
    degeneration; reading a boost out of the placeholder would inflate `ro`
    for a pair that is not actually degenerated.
    """
    devs = [_mos("m1", "s1")]
    assert _source_degeneration_r(devs, [_res("r1", "s1", "net_tail")], {}) == {}


def test_degeneration_ignores_resistors_off_the_pair_sources():
    """A compensation or bias resistor in the slot is not source degeneration."""
    devs = [_mos("m1", "s1")]
    rs = [_res("rc", "net_a", "net_b")]
    assert _source_degeneration_r(devs, rs, {"rc": 500.0}) == {}


# --------------------------------------------------------------------------- #
# Withholding a non-existent operating point (issue #148)
# --------------------------------------------------------------------------- #
def test_withheld_drops_gain_derived_metrics_only():
    kw = dict(cc_pf=2.0, gd_tail=1e-7, gd_output_load=1e-7,
              supply_currents=(10e-6, 25e-6))
    chain = _chain((1e-3, 1e6, 10e-6), (2e-3, 5e5, 25e-6), **kw)
    live, _ = evaluate_metrics(chain, _spec())
    dead, _ = evaluate_metrics(chain.withheld(), _spec())

    for k in ("gain_db", "gbw_hz", "phase_margin_deg", "cmrr_db", "psrr_db"):
        assert k in live and k not in dead
    # Slew and power do not depend on the small-signal operating point.
    for k in ("slew_rate_vps", "power_w"):
        assert dead[k] == pytest.approx(live[k])


# --------------------------------------------------------------------------- #
# Margins: positive always means "spec met"
# --------------------------------------------------------------------------- #
def test_margin_sign_is_positive_when_met_for_min_and_max_specs():
    chain = _chain((1e-3, 1e6), (2e-3, 5e5), cc_pf=2.0,
                   supply_currents=(10e-6,), swing_headroom=(0.2, 0.2))
    spec = _spec(gain_min_db=40, power_max_w=1.0, output_swing_min_v=1.0)
    m, margins = evaluate_metrics(chain, spec)

    assert margins["gain_db"] == pytest.approx(m["gain_db"] - 40)     # min spec
    assert margins["power_w"] == pytest.approx(1.0 - m["power_w"])    # max spec
    assert margins["output_swing_min_v"] == pytest.approx(1.0 - m["output_swing_min_v"])
    assert all(v > 0 for v in margins.values())


def test_unconstrained_metrics_get_no_margin():
    chain = _chain((1e-3, 1e6), (2e-3, 5e5), supply_currents=(10e-6,))
    m, margins = evaluate_metrics(chain, _spec())
    assert "gain_db" in m and "power_w" in m
    assert margins == {}


# --------------------------------------------------------------------------- #
# The chain transforms the resistor network applies
# --------------------------------------------------------------------------- #
def test_degeneration_scales_the_input_pair_only():
    chain = _chain((1e-3, 1e6), (2e-3, 5e5)).with_first_stage_gm(0.5)
    assert chain.stages[0].gm == pytest.approx(5e-4)
    assert chain.stages[1].gm == pytest.approx(2e-3)


def test_output_loading_lowers_the_last_stage_rout():
    chain = _chain((1e-3, 1e6), (2e-3, 1e6)).with_output_loading(1e-6)
    assert chain.stages[0].rout == pytest.approx(1e6)      # untouched
    assert chain.stages[1].rout == pytest.approx(5e5)      # 1e6 ∥ 1e6


def test_resistor_tail_only_applies_when_no_device_stack_was_found():
    """A resistor tail and a transistor tail are mutually exclusive."""
    assert _chain((1e-3, 1e6)).with_tail_conductance(1e-6).gd_tail == pytest.approx(1e-6)
    found = _chain((1e-3, 1e6), gd_tail=1e-9)
    assert found.with_tail_conductance(1e-6).gd_tail == pytest.approx(1e-9)


def test_transforms_do_not_mutate_the_original():
    chain = _chain((1e-3, 1e6), (2e-3, 5e5))
    chain.with_first_stage_gm(0.5).with_output_loading(1e-6).withheld()
    assert chain.stages[0].gm == pytest.approx(1e-3)
    assert chain.stages[1].rout == pytest.approx(5e5)
    assert chain.gain_measurable


# --------------------------------------------------------------------------- #
# Output swing headroom: the follower output stage's level shift
# --------------------------------------------------------------------------- #
class _BodyModel:
    """The one DeviceModel primitive the headroom walk needs."""

    def __init__(self, gamma=0.0, phi=0.7):
        self.gamma, self.phi = gamma, phi

    def body_vth_shift(self, dtype, vsb):
        return eq.body_vth_shift(self.gamma, self.phi, vsb)


def _dev(ref, dtype, d, g, s, b=None):
    rail = "gnd!" if dtype == "nmos" else "vdd!"
    return Device(ref=ref, type=dtype,
                  terminals={"d": d, "g": g, "s": s, "b": b or rail})


def _sz(ref, vdsat, vgs=0.8):
    return TransistorSizing(ref=ref, w_um=1.0, l_um=1.0, ids_a=1e-5,
                            vgs_v=vgs, vds_sat_v=vdsat)


# A second stage driving ``amp``: NMOS signal device, PMOS current-source load.
_SS = [_dev("mn_ss", "nmos", "amp", "mid", "gnd!"),
       _dev("mp_ss", "pmos", "amp", "net_bias5", "vdd!")]
_SS_SIZING = {"mn_ss": _sz("mn_ss", 0.10), "mp_ss": _sz("mp_ss", 0.15)}

_PMOS_FOLLOWER = [_dev("mp_f", "pmos", "gnd!", "amp", "out", b="out"),
                  _dev("mp_cs", "pmos", "out", "net_bias6", "vdd!")]
_NMOS_FOLLOWER = [_dev("mn_f", "nmos", "vdd!", "amp", "out"),
                  _dev("mn_cs", "nmos", "out", "net_bias6", "gnd!")]
_SIZING = {**_SS_SIZING,
           "mp_f": _sz("mp_f", 0.1, vgs=-0.80), "mp_cs": _sz("mp_cs", 0.20),
           "mn_f": _sz("mn_f", 0.1, vgs=0.70), "mn_cs": _sz("mn_cs", 0.25)}


def _view(output_stage=None, slot="output_stage"):
    slots = {"second_stage": list(_SS)}
    if output_stage:
        slots[slot] = list(output_stage)
    return CircuitView(
        slot_transistors=slots,
        all_transistors={d.ref: (d, sl) for sl, devs in slots.items() for d in devs})


def test_unbuffered_headroom_is_the_second_stage_vdsat():
    head = output_swing_headroom(_view(), _SS_SIZING, _BodyModel(), _spec())
    assert head == pytest.approx((0.15, 0.10))


def test_pmos_follower_lifts_the_low_edge_by_its_vgs():
    """out = in + |Vgs|: the low edge is the driver's Vdsat plus |Vgs|; the
    high edge is the follower's own current source leaving saturation.  The
    body is tied to the source, so no body effect even with gamma > 0."""
    head = output_swing_headroom(_view(_PMOS_FOLLOWER), _SIZING,
                                 _BodyModel(gamma=0.5), _spec())
    assert head == pytest.approx((0.20, 0.10 + 0.80))


def test_nmos_follower_drops_the_high_edge_by_its_vgs():
    head = output_swing_headroom(_view(_NMOS_FOLLOWER), _SIZING, _BodyModel(), _spec())
    assert head == pytest.approx((0.15 + 0.70, 0.25))


def test_nmos_follower_body_effect_is_solved_at_the_swing_edge():
    """Bulk at Vss: at the high edge the source sits (Vdd − headroom) above
    the bulk, and the raised Vth must be consistent with that edge."""
    spec, model = _spec(), _BodyModel(gamma=0.5, phi=0.7)
    hi, lo = output_swing_headroom(_view(_NMOS_FOLLOWER), _SIZING, model, spec)
    base = 0.15 + 0.70
    assert hi > base + 0.2                       # body effect is not negligible
    assert hi == pytest.approx(base + model.body_vth_shift("nmos", spec.vdd - hi),
                               abs=1e-6)
    assert lo == pytest.approx(0.25)


def test_fd_follower_is_read_off_the_p_leg():
    head = output_swing_headroom(_view(_PMOS_FOLLOWER, slot="output_stage_p"),
                                 _SIZING, _BodyModel(), _spec())
    assert head == pytest.approx((0.20, 0.90))


def test_follower_headroom_is_none_when_its_driver_is_unsized():
    sizing = {k: v for k, v in _SIZING.items() if k != "mn_ss"}
    hi, lo = output_swing_headroom(_view(_PMOS_FOLLOWER), sizing, _BodyModel(), _spec())
    assert hi == pytest.approx(0.20) and lo is None


@pytest.mark.parametrize("follower,spec_kw,feasible", [
    # PMOS follower: low edge at 0.90 V.
    (_PMOS_FOLLOWER, dict(output_swing_min_v=0.3), False),
    (_PMOS_FOLLOWER, dict(output_swing_min_v=0.9 - _FOLLOWER_SWING_TOL_V + 0.01), True),
    (_PMOS_FOLLOWER, dict(output_swing_min_v=1.0), True),
    (_PMOS_FOLLOWER, dict(output_swing_max_v=4.99), True),   # other side not gated
    # NMOS follower: high edge at 5 − 0.85 = 4.15 V.
    (_NMOS_FOLLOWER, dict(output_swing_max_v=4.8), False),
    (_NMOS_FOLLOWER, dict(output_swing_max_v=4.15 + _FOLLOWER_SWING_TOL_V - 0.01), True),
    (_NMOS_FOLLOWER, dict(output_swing_min_v=0.01), True),
    (None, dict(output_swing_min_v=0.01, output_swing_max_v=4.99), True),
])
def test_follower_swing_gate(follower, spec_kw, feasible):
    warnings, ok = check_follower_swing(_view(follower), _SIZING, _BodyModel(),
                                        _spec(**spec_kw))
    assert ok is feasible
    assert bool(warnings) is not feasible
    if warnings:
        assert "source-follower" in warnings[0] and "swing spec" in warnings[0]


# --------------------------------------------------------------------------- #
# Output swing headroom: a DDA CMFB's sense range (issue #272)
# --------------------------------------------------------------------------- #
# FD second stages driving outp/outn, and the two CMFB forms sensing them.
_FD_SS = [_dev("mn_ssp", "nmos", "outp", "x1", "gnd!"),
          _dev("mp_ssp", "pmos", "outp", "net_bias5", "vdd!"),
          _dev("mn_ssn", "nmos", "outn", "x2", "gnd!"),
          _dev("mp_ssn", "pmos", "outn", "net_bias5", "vdd!")]
_FD_SS_SIZING = {r: _sz(r, v) for r, v in (("mn_ssp", 0.10), ("mp_ssp", 0.15),
                                           ("mn_ssn", 0.10), ("mp_ssn", 0.15))}
_DDA = [_dev("m1_cmfb", "nmos", "da", "vcm_ref", "t1"),
        _dev("m2_cmfb", "nmos", "co", "outp", "t1"),
        _dev("m3_cmfb", "nmos", "da", "vcm_ref", "t2"),
        _dev("m4_cmfb", "nmos", "co", "outn", "t2"),
        _dev("m5_cmfb", "pmos", "da", "da", "vdd!"),
        _dev("m6_cmfb", "pmos", "co", "co", "vdd!"),
        _dev("m7_cmfb", "nmos", "t1", "net_bias4", "gnd!"),
        _dev("m8_cmfb", "nmos", "t2", "net_bias4", "gnd!")]
_RS = [_dev("m1_cmfb", "nmos", "d1", "sense", "t"),
       _dev("m2_cmfb", "nmos", "co", "vcm_ref", "t"),
       _dev("m3_cmfb", "pmos", "d1", "d1", "vdd!"),
       _dev("m4_cmfb", "pmos", "co", "co", "vdd!"),
       _dev("m5_cmfb", "nmos", "t", "net_bias4", "gnd!")]
_BIAS_DIODE = [_dev("mn4_bias_gen", "nmos", "net_bias4", "net_bias4", "gnd!")]


def _cmfb_sizing(cmfb, sense_vdsat=0.2):
    return {**_FD_SS_SIZING, **{d.ref: _sz(d.ref, sense_vdsat) for d in cmfb}}


def _fd_view(cmfb):
    slots = {"second_stage_p": _FD_SS[:2], "second_stage_n": _FD_SS[2:],
             "bias_gen": list(_BIAS_DIODE), "cmfb": list(cmfb)}
    return CircuitView(
        slot_transistors=slots,
        all_transistors={d.ref: (d, sl) for sl, devs in slots.items() for d in devs})


def test_dda_sense_range_is_root2_vdsat_of_the_sense_pair():
    """Each sense pair has steered its whole tail at ±√2·Vov; the vref-side
    devices, diodes and tails (gated by internal or bias nets) do not count."""
    sizing = _cmfb_sizing(_DDA)
    sizing["m1_cmfb"] = _sz("m1_cmfb", 0.05)     # vref side: not a sense device
    assert cmfb_sense_range(_fd_view(_DDA), sizing) == pytest.approx(
        math.sqrt(2) * 0.2)


def test_dda_sense_range_caps_the_swing_around_mid_rail():
    """5 V supply, ±0.283 V range: the outputs are CM-regulated over
    2.217-2.783 V, far inside the second stage's Vdsat limits."""
    rng = math.sqrt(2) * 0.2
    hi, lo = output_swing_headroom(_fd_view(_DDA), _cmfb_sizing(_DDA),
                                   _BodyModel(), _spec())
    assert hi == pytest.approx(2.5 - rng) and lo == pytest.approx(2.5 - rng)


def test_wide_dda_sense_range_leaves_the_stage_limits():
    hi, lo = output_swing_headroom(_fd_view(_DDA), _cmfb_sizing(_DDA, 2.0),
                                   _BodyModel(), _spec())
    assert (hi, lo) == pytest.approx((0.15, 0.10))


def test_resistive_sense_cmfb_does_not_limit_the_swing():
    """Its pair senses the resistor-averaged CM, never an output directly."""
    view, sizing = _fd_view(_RS), _cmfb_sizing(_RS)
    assert cmfb_sense_range(view, sizing) is None
    assert output_swing_headroom(view, sizing, _BodyModel(), _spec()) == \
        pytest.approx((0.15, 0.10))


_DDA_EDGE = math.sqrt(2) * 0.2          # 2.5 ± 0.283 V on a 5 V supply


@pytest.mark.parametrize("spec_kw,feasible", [
    (dict(output_swing_max_v=4.5), False),
    (dict(output_swing_min_v=0.5), False),
    (dict(output_swing_max_v=2.5 + _DDA_EDGE + _CMFB_SENSE_SWING_TOL_V - 0.01,
          output_swing_min_v=2.5 - _DDA_EDGE - _CMFB_SENSE_SWING_TOL_V + 0.01),
     True),
    (dict(output_swing_max_v=2.6, output_swing_min_v=2.4), True),
    (dict(), True),
])
def test_cmfb_sense_swing_gate(spec_kw, feasible):
    warnings, ok = check_cmfb_sense_swing(_fd_view(_DDA), _cmfb_sizing(_DDA),
                                          _spec(**spec_kw))
    assert ok is feasible
    assert bool(warnings) is not feasible
    if warnings:
        assert "difference-difference CMFB" in warnings[0]


def test_cmfb_sense_swing_gate_passes_resistive_sense():
    assert check_cmfb_sense_swing(
        _fd_view(_RS), _cmfb_sizing(_RS),
        _spec(output_swing_max_v=4.9, output_swing_min_v=0.1)) == ([], True)


# --------------------------------------------------------------------------- #
# Output swing headroom: an FD pair swings symmetrically about Vocm (#281)
# --------------------------------------------------------------------------- #
def _fd_follower_view(output_stage):
    slots = {"second_stage_p": list(_SS), "output_stage_p": list(output_stage)}
    return CircuitView(
        slot_transistors=slots,
        all_transistors={d.ref: (d, sl) for sl, devs in slots.items() for d in devs})


def test_fd_nmos_follower_shift_bounds_both_edges():
    """outn = Vdd − outp around a mid-rail CM: the follower holds the high
    edge 0.85 V below Vdd, so the low edge stops 0.85 V above Vss, not at the
    current sink's 0.25 V (gf180: predicted 0.17 V, SPICE 0.96 V)."""
    head = output_swing_headroom(_fd_follower_view(_NMOS_FOLLOWER), _SIZING,
                                 _BodyModel(), _spec())
    assert head == pytest.approx((0.15 + 0.70, 0.15 + 0.70))


def test_fd_pmos_follower_shift_bounds_both_edges():
    head = output_swing_headroom(_fd_follower_view(_PMOS_FOLLOWER), _SIZING,
                                 _BodyModel(), _spec())
    assert head == pytest.approx((0.10 + 0.80, 0.10 + 0.80))


def test_single_ended_follower_edges_stay_independent():
    head = output_swing_headroom(_view(_NMOS_FOLLOWER), _SIZING, _BodyModel(), _spec())
    assert head == pytest.approx((0.85, 0.25))


def test_unbuffered_fd_edges_stay_independent():
    """Its Vdsat limits are soft (SPICE tracks almost to both rails), so
    mirroring the larger one would only make the other edge pessimistic."""
    head = output_swing_headroom(_fd_view(_RS), _cmfb_sizing(_RS), _BodyModel(), _spec())
    assert head == pytest.approx((0.15, 0.10))


def test_fd_symmetry_leaves_an_unplaced_side_unplaced():
    sizing = {k: v for k, v in _SIZING.items() if k != "mn_ss"}
    hi, lo = output_swing_headroom(_fd_follower_view(_PMOS_FOLLOWER), sizing,
                                   _BodyModel(), _spec())
    assert hi == pytest.approx(0.20) and lo is None


@pytest.mark.parametrize("follower,spec_kw,feasible", [
    # NMOS follower: high edge 4.15 V, mirrored low edge 0.85 V.
    (_NMOS_FOLLOWER, dict(output_swing_min_v=0.3), False),
    (_NMOS_FOLLOWER, dict(output_swing_min_v=0.85 - _FOLLOWER_SWING_TOL_V + 0.01), True),
    (_NMOS_FOLLOWER, dict(output_swing_max_v=4.8), False),
    # PMOS follower: low edge 0.90 V, mirrored high edge 4.10 V.
    (_PMOS_FOLLOWER, dict(output_swing_max_v=4.8), False),
    (_PMOS_FOLLOWER, dict(output_swing_max_v=4.1 + _FOLLOWER_SWING_TOL_V - 0.01), True),
])
def test_fd_follower_swing_gate_checks_the_mirrored_edge(follower, spec_kw, feasible):
    warnings, ok = check_follower_swing(_fd_follower_view(follower), _SIZING,
                                        _BodyModel(), _spec(**spec_kw))
    assert ok is feasible
    assert bool(warnings) is not feasible


def test_fd_follower_swing_gate_names_both_missed_edges():
    warnings, ok = check_follower_swing(
        _fd_follower_view(_NMOS_FOLLOWER), _SIZING, _BodyModel(),
        _spec(output_swing_max_v=4.8, output_swing_min_v=0.3))
    assert not ok
    assert "high swing to 4.15 V" in warnings[0]
    assert "low swing to 0.85 V" in warnings[0]


def test_evaluate_metrics_reads_the_headroom_off_each_rail():
    chain = _chain((1e-3, 1e6), (2e-3, 5e5), swing_headroom=(0.85, 0.25))
    spec = _spec(output_swing_max_v=4.5, output_swing_min_v=0.3)
    m, _ = evaluate_metrics(chain, spec)
    assert m["output_swing_max_v"] == pytest.approx(5.0 - 0.85)
    assert m["output_swing_min_v"] == pytest.approx(0.25)


def test_body_vth_shift():
    assert eq.body_vth_shift(0.0, 0.7, 1.0) == 0.0
    assert eq.body_vth_shift(0.5, 0.7, -0.3) == 0.0          # forward bias clamped
    assert eq.body_vth_shift(0.5, 0.7, 1.0) == pytest.approx(
        0.5 * (math.sqrt(1.7) - math.sqrt(0.7)))


# --------------------------------------------------------------------------- #
# Single-ended CMRR / PSRR+: the DC nodal solve (issue #290)
# --------------------------------------------------------------------------- #
def _pair(dtype, d1, d2, tail):
    return [Device("m1", dtype, {"d": d1, "g": "in1", "s": tail, "b": tail}),
            Device("m2", dtype, {"d": d2, "g": "in2", "s": tail, "b": tail})]


def test_resistor_pair_rejection_is_gm_times_the_tail_resistance():
    """Resistor loads and a resistor tail, ideal devices.  The bench drives one
    input, so Adm = gm²·RD/(1/RT + 2gm) and Acm = gm·RD·(1/RT)/(1/RT + 2gm):
    CMRR is exactly gm·RT, twice the gm/(2·gd_tail) of the closed form.  VDD
    reaches the output through RD at unity gain, so PSRR+ is Adm itself."""
    gm, rd, rt = 1e-4, 50e3, 20e3
    mosfets = _pair("nmos", "d1", "out", "tail")
    params = {d.ref: (gm, 0.0, 0.0) for d in mosfets}
    resistors = [("vdd!", "d1", rd), ("vdd!", "out", rd), ("tail", "gnd!", rt)]
    cmrr, psrr = _se_rejection_db(mosfets, params, resistors, ("in1", "in2"), "out")
    assert cmrr == pytest.approx(20 * math.log10(gm * rt))
    assert psrr == pytest.approx(20 * math.log10(gm * gm * rd / (1 / rt + 2 * gm)))


# A five-transistor OTA at one-stage #4's ngspice operating point (GF180, the
# #256 sample): PMOS pair, NMOS mirror, the output held at Vcm.  The pair's
# drains sit at different levels -- the mirror's diode node and the output --
# so the two halves' gds differ by 43 %.
_OTA5 = [*_pair("pmos", "d1", "out", "tail"),
         Device("m3", "nmos", {"d": "d1", "g": "d1", "s": "gnd!", "b": "gnd!"}),
         Device("m4", "nmos", {"d": "out", "g": "d1", "s": "gnd!", "b": "gnd!"}),
         Device("mt", "pmos", {"d": "tail", "g": "gnd!", "s": "vdd!", "b": "vdd!"})]


def _ota5_params(gd_tail):
    return {"m1": (1.94e-4, 4.44e-7, 0.0), "m2": (2.01e-4, 6.34e-7, 0.0),
            "m3": (0.99e-4, 6.28e-7, 0.0), "m4": (1.02e-4, 4.88e-7, 0.0),
            "mt": (2.01e-4, gd_tail, 0.0)}


def test_mirror_load_cmrr_is_set_by_the_pair_drain_imbalance():
    """On a mirror load the tail barely matters: the mirror cancels the
    common-mode current the tail lets through.  What is left is the imbalance.
    A common-mode input moves the pair's sources, and the halves' unequal gds
    turn that into output current: CMRR ≈ gm1/|gds2 − gds1|, 60.4 dB here.
    ngspice measured 61.4 dB, and 61.3 dB with a cascode tail; the closed form
    gm1/(2·gd_tail) read 50.8 and 107.9 dB for those two tails."""
    real, _ = _se_rejection_db(_OTA5, _ota5_params(4.55e-7), [], ("in1", "in2"), "out")
    ideal, _ = _se_rejection_db(_OTA5, _ota5_params(0.0), [], ("in1", "in2"), "out")
    assert real == pytest.approx(20 * math.log10(1.94e-4 / (6.34e-7 - 4.44e-7)), abs=1.5)
    assert real == pytest.approx(61.4, abs=1.0)
    assert ideal == pytest.approx(real, abs=0.5)


def test_se_rejection_is_none_without_the_output_or_a_solution():
    mosfets = _pair("nmos", "d1", "out", "tail")
    params = {d.ref: (1e-4, 0.0, 0.0) for d in mosfets}
    assert _se_rejection_db(mosfets, params, [], ("in1", "in2"), "nowhere") == (None, None)
    # No tail and no loads: nothing ties the nodes down, the network is singular.
    assert _se_rejection_db(mosfets, params, [], ("in1", "in2"), "out") == (None, None)


def test_evaluate_metrics_reports_the_nodal_rejection_when_the_chain_has_it():
    """A single-ended chain carries the nodal (CMRR, PSRR+); a chain without it
    (fully differential) keeps the closed forms; a withheld one reports neither."""
    kw = dict(cc_pf=2.0, gd_tail=1e-7, gd_output_load=1e-6)
    se = _chain((1e-3, 1e6), (2e-3, 5e5), se_rejection_db=(83.0, 91.0), **kw)
    m = evaluate_metrics(se, _spec())[0]
    assert (m["cmrr_db"], m["psrr_db"]) == (83.0, 91.0)
    fd = evaluate_metrics(_chain((1e-3, 1e6), (2e-3, 5e5), **kw), _spec())[0]
    assert fd["cmrr_db"] == pytest.approx(eq.cmrr_db(1e-3, 1e-7))
    assert fd["psrr_db"] == pytest.approx(eq.psrr_db_approx(2e-3, 1e-6))
    withheld = evaluate_metrics(se.withheld(), _spec())[0]
    assert "cmrr_db" not in withheld and "psrr_db" not in withheld


# A telescopic cascode stacks a same-type cascode on each pair drain; a folded
# one hangs an opposite-type cascode there; a mirror sources from the rail.
_NPAIR = _pair("nmos", "d1", "d2", "tail")
_TELESCOPIC = [Device("mc1", "nmos", {"d": "x1", "g": "b1", "s": "d1", "b": "gnd!"}),
               Device("mc2", "nmos", {"d": "out", "g": "b1", "s": "d2", "b": "gnd!"})]
_FOLDED = [Device("mc1", "pmos", {"d": "x1", "g": "b1", "s": "d1", "b": "vdd!"}),
           Device("mc2", "pmos", {"d": "out", "g": "b1", "s": "d2", "b": "vdd!"})]
_MIRROR = [Device("ml1", "pmos", {"d": "d1", "g": "d1", "s": "vdd!", "b": "vdd!"}),
           Device("ml2", "pmos", {"d": "d2", "g": "d1", "s": "vdd!", "b": "vdd!"})]


@pytest.mark.parametrize("load,telescopic", [
    (_TELESCOPIC, True), (_FOLDED, False), (_MIRROR, False), ([], False)])
def test_telescopic_cascode_load_is_a_same_type_cascode_on_the_pair_drain(load, telescopic):
    assert _telescopic_cascode_load(_NPAIR, load) is telescopic


def _sized_chain(monkeypatch, template, variants):
    """``(metrics, chain)`` of a GF180 sizing, the chain the evaluator built."""
    import circuitgenome.sizer.gmid.evaluate as evaluate
    from circuitgenome.recognizer import assign_slots, parse, recognize
    from circuitgenome.sizer import load_tech, size_circuit
    from circuitgenome.synthesizer.loader import load_modules, load_topologies
    from circuitgenome.synthesizer.netlist import to_flat_spice
    from circuitgenome.synthesizer.synthesizer import enumerate_circuits

    built = []
    real = evaluate.build_stage_chain
    monkeypatch.setattr(evaluate, "build_stage_chain",
                        lambda *a, **kw: built.append(real(*a, **kw)) or built[-1])
    topo = next(t for t in load_topologies() if t.name == template)
    circ = next(c for c in enumerate_circuits(topo, load_modules())
                if all(c.variant_map.get(k) and c.variant_map[k].name == v
                       for k, v in variants.items()))
    parsed = parse(to_flat_spice(circ))
    sr = recognize(parsed)
    spec = SizingSpec(vdd=3.3, vss=0.0, ibias=20e-6, cl=5e-12, gain_min_db=50,
                      gbw_min_hz=1e6, phase_margin_min_deg=55)
    result = size_circuit(parsed, sr, assign_slots(sr, topo), topo,
                          load_tech("gf180mcu"), spec)
    return result.metrics, built[-1]


@pytest.mark.parametrize("template,load", [
    ("one_stage_opamp", "telescopic_cascode_load_pmos"),
    ("one_stage_opamp", "telescopic_cascode_load_wideswing_nmos"),
    ("two_stage_opamp_single_ended", "telescopic_cascode_load_wideswing_pmos"),
])
def test_telescopic_load_keeps_the_closed_forms(monkeypatch, template, load):
    """The nodal solve cannot see the ~1 mV drain imbalance that sets a
    telescopic cascode's rejection floor and over-predicts it (CMRR +47 dB,
    PSRR+ +19 dB median on GF180), so these keep the under-predicting closed
    forms until the level estimate can (issue #290)."""
    m, chain = _sized_chain(monkeypatch, template, {"load": load})
    assert chain.se_rejection_db is None
    gm1 = chain.stages[0].gm
    gm_out = chain.stages[1].gm if len(chain.stages) > 1 else chain.k_fs * gm1
    assert m["cmrr_db"] == pytest.approx(eq.cmrr_db(gm1, chain.gd_tail))
    assert m["psrr_db"] == pytest.approx(eq.psrr_db_approx(gm_out, chain.gd_output_load))


def test_mirror_load_gets_the_nodal_rejection(monkeypatch):
    m, chain = _sized_chain(monkeypatch, "one_stage_opamp", {
        "input_pair": "differential_pair_pmos", "load": "active_load_nmos",
        "tail_current": "current_mirror_tail_pmos"})
    assert chain.se_rejection_db is not None
    assert (m["cmrr_db"], m["psrr_db"]) == chain.se_rejection_db
