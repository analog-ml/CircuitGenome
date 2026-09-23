"""Unit tests for CMA-ES sizing refinement (ngspice is injected/faked)."""
from __future__ import annotations

import numpy as np
import pytest

from circuitgenome.sizer import (
    CmaEsConfig,
    SizingResult,
    SizingSpec,
    TransistorSizing,
    load_tech,
    figures_of_merit,
    minimize_cmaes,
    metric_sanity_problem,
    refine_cmaes,
)
from circuitgenome.sizer.cmaes import default_width_groups


def _sizing(ref: str, width: float) -> TransistorSizing:
    return TransistorSizing(ref, width, 1.0, 10e-6, 0.7, 0.2)


def test_default_budget_is_high_but_local():
    config = CmaEsConfig()
    assert config.population_size == 16
    assert config.generations == 80
    assert 1 + config.population_size * config.generations == 1281
    assert (config.min_scale, config.max_scale) == (0.6, 1.7)
    assert config.objective == "fom"


def test_opamp_figures_of_merit():
    spec = SizingSpec(vdd=1.0, vss=0.0, ibias=10e-6, cl=20e-12)
    foms = figures_of_merit(
        {"power_w": 50e-6, "gbw_hz": 5e6, "slew_rate_vps": 2e6}, spec)
    assert foms["iq_a"] == 50e-6
    assert foms["fom_s_per_v"] == pytest.approx(2.0)
    assert foms["fom_l"] == pytest.approx(0.8)


def test_metric_sanity_rejects_transient_spike_slew():
    spec = SizingSpec(vdd=1.0, vss=0.0, ibias=10e-6, cl=20e-12)
    sizing = SizingResult(
        transistors={"m1": _sizing("m1", 10.0),
                     "m2": _sizing("m2", 10.0)},
        cc_pf=1.0, metrics={}, margins={}, solver_status="GMID",
    )
    good = {"power_w": 50e-6, "gain_db": 60.0, "gbw_hz": 5e6,
            "phase_margin_deg": 60.0, "slew_rate_vps": 2e6,
            "output_swing_max_v": 0.9, "output_swing_min_v": 0.1}
    assert metric_sanity_problem(good, sizing, spec) is None
    bad = dict(good, slew_rate_vps=4.76e9)
    assert "implausible load current" in metric_sanity_problem(bad, sizing, spec)


def test_minimize_cmaes_converges_on_sphere():
    result = minimize_cmaes(
        lambda x: float(np.dot(x, x)),
        [2.0, -1.5, 0.5],
        CmaEsConfig(generations=60, sigma=0.5, seed=4, patience=20),
        bounds=([-4.0] * 3, [4.0] * 3),
    )
    assert result.value < 1e-3
    assert result.evaluations > 1
    assert result.history[-1] <= result.history[0]


def test_default_width_groups_preserve_mirror_ratio():
    netlist = """.subckt dut ibias out vdd! gnd!
mn1_bias_gen ibias ibias gnd! gnd! nmos
mn2_bias_gen out ibias gnd! gnd! nmos
.ends
"""
    initial = SizingResult(
        transistors={"mn1_bias_gen": _sizing("mn1_bias_gen", 10.0),
                     "mn2_bias_gen": _sizing("mn2_bias_gen", 20.0)},
        cc_pf=None, metrics={}, margins={}, solver_status="GMID",
    )
    assert default_width_groups(netlist, initial) == (
        ("mn1_bias_gen", "mn2_bias_gen"),)


def test_refine_cmaes_scales_group_and_uses_injected_simulator():
    netlist = """.subckt dut ibias out vdd! gnd!
mn1_bias_gen ibias ibias gnd! gnd! nmos
mn2_bias_gen out ibias gnd! gnd! nmos
.ends
"""
    initial = SizingResult(
        transistors={"mn1_bias_gen": _sizing("mn1_bias_gen", 10.0),
                     "mn2_bias_gen": _sizing("mn2_bias_gen", 20.0)},
        cc_pf=None, metrics={}, margins={}, solver_status="GMID",
    )
    calls = []

    def fake_sim(_text, sizing, _tech, _spec, corner=None):
        calls.append((sizing.transistors["mn1_bias_gen"].w_um, corner))
        return {"gain_db": 20.0, "power_w": 1e-3}

    refined = refine_cmaes(
        netlist, initial, load_tech("generic"),
        SizingSpec(vdd=5.0, vss=0.0, ibias=10e-6, cl=1e-12),
        CmaEsConfig(generations=12, sigma=0.4, min_scale=0.5,
                    max_scale=2.0, optimize_compensation=False,
                    seed=2, patience=6),
        simulator=fake_sim,
    )
    w1 = refined.sizing.transistors["mn1_bias_gen"].w_um
    w2 = refined.sizing.transistors["mn2_bias_gen"].w_um
    assert calls
    assert w1 <= 10.0
    assert w2 / w1 == 2.0
    assert refined.evaluations >= len(calls)  # snapped duplicate widths use cache
    assert refined.spice_metrics["gain_db"] == 20.0
    assert refined.rejections == {}
