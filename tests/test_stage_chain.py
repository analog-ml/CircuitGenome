"""Tests for the stage chain and the metric algebra over it.

These sit between the single-formula tests in ``test_sizer.py`` and the
end-to-end ``size_circuit`` runs: a chain is a handful of floats, so metric
behaviour can be pinned without synthesizing and recognizing a circuit first.
"""
import math

import pytest

from circuitgenome.sizer import SizingSpec
from circuitgenome.sizer.physics import equations as eq
from circuitgenome.sizer.physics.metrics import evaluate_metrics
from circuitgenome.sizer.physics.stage_chain import Stage, StageChain

INF = float("inf")


def _spec(**kw):
    base = dict(vdd=5.0, vss=0.0, ibias=10e-6, cl=20e-12)
    base.update(kw)
    return SizingSpec(**base)


def _chain(*stages, **kw):
    return StageChain(stages=tuple(Stage(gm, rout) for gm, rout in stages), **kw)


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

    comp = evaluate_metrics(_chain((1e-3, 1e6), (2e-3, 5e5), cc_pf=2.0), _spec())[0]
    assert comp["gbw_hz"] == pytest.approx(eq.unity_gain_bw(1e-3, 2e-12))
    assert comp["slew_rate_vps"] == pytest.approx(eq.slew_rate_vps(10e-6, 2e-12))


def test_k_fs_scales_gbw_but_not_cmrr():
    """k_fs is the loop transconductance; CMRR sees the raw device gm."""
    kw = dict(cc_pf=2.0, gd_tail=1e-7)
    full = evaluate_metrics(_chain((1e-3, 1e6), (2e-3, 5e5), **kw), _spec())[0]
    half = evaluate_metrics(_chain((1e-3, 1e6), (2e-3, 5e5), k_fs=0.5, **kw), _spec())[0]
    assert half["gbw_hz"] == pytest.approx(full["gbw_hz"] / 2.0)
    assert half["cmrr_db"] == pytest.approx(full["cmrr_db"])


def test_three_stage_uses_the_three_stage_phase_margin():
    chain = _chain((1e-3, 1e6), (2e-3, 5e5), (3e-3, 2e5), cc_pf=2.0, cc2_pf=0.5)
    m, _ = evaluate_metrics(chain, _spec())
    assert m["phase_margin_deg"] == pytest.approx(
        eq.phase_margin_three_stage_deg(1e-3, 2e-3, 3e-3, 2e-12, 0.5e-12, 20e-12))


# --------------------------------------------------------------------------- #
# Withholding a non-existent operating point (issue #148)
# --------------------------------------------------------------------------- #
def test_withheld_drops_gain_derived_metrics_only():
    kw = dict(cc_pf=2.0, gd_tail=1e-7, gd_stage2_load=1e-7,
              supply_currents=(10e-6, 25e-6))
    chain = _chain((1e-3, 1e6), (2e-3, 5e5), **kw)
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
                   supply_currents=(10e-6,), swing_vdsat=(0.2, 0.2))
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
