"""Tests for the Initial Sizing module (circuitgenome/sizer)."""
from __future__ import annotations
import math
import pytest

from circuitgenome.sizer import load_tech, size_circuit, SizingSpec
from circuitgenome.sizer.equations import (
    cmrr_db,
    gd,
    gm,
    open_loop_gain_db,
    phase_margin_two_stage_deg,
    rout,
    slew_rate_vps,
    unity_gain_bw,
    vds_sat,
    vgs_from_ids,
)
from circuitgenome.sizer.models import TechParams
from circuitgenome.synthesizer.loader import load_modules, load_topologies
from circuitgenome.synthesizer.synthesizer import enumerate_circuits
from circuitgenome.synthesizer.netlist import to_flat_spice
from circuitgenome.recognizer import parse, recognize, assign_slots


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _tech():
    return load_tech()  # built-in generic (0.25µm-like params)


def _make_circuit(topology_name: str, variant_filter: dict[str, str] | None = None):
    modules = load_modules()
    topologies = load_topologies()
    topology = next(t for t in topologies if t.name == topology_name)
    for circuit in enumerate_circuits(topology, modules):
        if variant_filter is None:
            return topology, circuit
        if all(circuit.variant_map.get(k, {}).name == v for k, v in variant_filter.items()):
            return topology, circuit
    raise ValueError(f"No matching circuit found for {topology_name} with {variant_filter}")


def _fbr(topology_name: str, variant_filter: dict[str, str] | None = None):
    topology, circuit = _make_circuit(topology_name, variant_filter)
    spice = to_flat_spice(circuit)
    parsed = parse(spice)
    sr_result = recognize(parsed)
    fbr_result = assign_slots(sr_result, topology)
    return parsed, sr_result, fbr_result, topology


# ---------------------------------------------------------------------------
# Tech loader
# ---------------------------------------------------------------------------

def test_load_tech_defaults():
    tech = load_tech()
    assert tech.name == "generic_parameterized"
    assert tech.nmos.mu_cox == pytest.approx(270e-6)
    assert tech.pmos.mu_cox == pytest.approx(90e-6)
    assert tech.nmos.vth == pytest.approx(0.5)
    assert tech.pmos.vth == pytest.approx(-0.5)
    assert tech.width.min == pytest.approx(1.0)
    assert tech.width.max == pytest.approx(600.0)
    assert tech.length.step == pytest.approx(1.0)
    assert tech.cap.min == pytest.approx(0.1)


# ---------------------------------------------------------------------------
# Level-1 MOSFET equations
# ---------------------------------------------------------------------------

def test_gm_formula():
    # gm = sqrt(2 * mu_cox * (W/L) * IDS)
    # NMOS: mu_cox=270e-6, W=21µm, L=2µm, IDS=5µA
    result = gm(270e-6, 21.0, 2.0, 5e-6)
    expected = math.sqrt(2 * 270e-6 * (21 / 2) * 5e-6)
    assert result == pytest.approx(expected, rel=1e-6)


def test_gm_pmos():
    # PMOS: mu_cox=90e-6, W=21µm, L=2µm, IDS=5µA (sign agnostic)
    result = gm(90e-6, 21.0, 2.0, 5e-6)
    expected = math.sqrt(2 * 90e-6 * (21 / 2) * 5e-6)
    assert result == pytest.approx(expected, rel=1e-6)
    assert result > 0


def test_gd_formula():
    result = gd(0.04, 5e-6)
    assert result == pytest.approx(0.04 * 5e-6)


def test_rout_formula():
    result = rout(0.04 * 5e-6, 0.05 * 5e-6)
    expected = 1 / ((0.04 + 0.05) * 5e-6)
    assert result == pytest.approx(expected, rel=1e-6)


def test_vgs_from_ids_nmos():
    # NMOS, should return positive VGS > Vth
    vgs = vgs_from_ids(270e-6, 21.0, 2.0, 5e-6, 0.5)
    assert vgs > 0.5
    # Round-trip: plug back in and verify IDS
    vod = vgs - 0.5
    ids_check = (270e-6 / 2) * (21 / 2) * vod ** 2
    assert ids_check == pytest.approx(5e-6, rel=0.01)


def test_vgs_from_ids_pmos():
    # PMOS, should return negative VGS (|VGS| > |Vth|=0.5)
    vgs = vgs_from_ids(90e-6, 21.0, 2.0, 5e-6, -0.5)
    assert vgs < -0.5


def test_vds_sat_positive():
    result = vds_sat(90e-6, 21.0, 2.0, 5e-6)
    assert result > 0
    # Should equal |VGS - Vth|
    vgs = vgs_from_ids(90e-6, 21.0, 2.0, 5e-6, -0.5)
    assert result == pytest.approx(abs(vgs - (-0.5)), rel=1e-6)


def test_open_loop_gain_two_stage():
    gm1_val = gm(90e-6, 21.0, 2.0, 5e-6)   # input pair (PMOS)
    gd1_top = gd(0.04, 5e-6)                # load (NMOS)
    gd1_bot = gd(0.05, 5e-6)                # input pair (PMOS)
    rout1 = rout(gd1_top, gd1_bot)

    gm2_val = gm(270e-6, 21.0, 1.0, 25e-6)  # second stage NMOS
    gd2_n = gd(0.04, 25e-6)
    gd2_p = gd(0.05, 25e-6)
    rout2 = rout(gd2_n, gd2_p)

    gain = open_loop_gain_db([gm1_val * rout1, gm2_val * rout2])
    assert gain > 60  # should be substantial (>60 dB)


def test_unity_gain_bw():
    gm1_val = gm(90e-6, 21.0, 2.0, 5e-6)
    cc_f = 4.5e-12
    gbw = unity_gain_bw(gm1_val, cc_f)
    # GBW = gm1 / (2π·Cc)
    expected = gm1_val / (2 * math.pi * cc_f)
    assert gbw == pytest.approx(expected, rel=1e-6)
    assert gbw > 1e6  # > 1 MHz for these dimensions


def test_phase_margin_formula():
    # PM = 90 - arctan(gm1 * CL / (gm2 * Cc))
    gm1_val = 70e-6   # A/V
    gm2_val = 200e-6  # A/V
    cc_f = 4e-12
    cl_f = 20e-12
    pm = phase_margin_two_stage_deg(gm1_val, gm2_val, cc_f, cl_f)
    expected = 90 - math.degrees(math.atan(gm1_val * cl_f / (gm2_val * cc_f)))
    assert pm == pytest.approx(expected, rel=1e-6)
    assert 0 < pm < 90


def test_slew_rate():
    ibias = 10e-6
    cc_f = 4e-12
    sr = slew_rate_vps(ibias, cc_f)
    assert sr == pytest.approx(ibias / cc_f)


def test_cmrr():
    gm1_val = gm(90e-6, 21.0, 2.0, 5e-6)
    gd_tail = gd(0.05, 10e-6)
    result = cmrr_db(gm1_val, gd_tail)
    expected = 20 * math.log10(gm1_val / (2 * gd_tail))
    assert result == pytest.approx(expected, rel=1e-6)
    assert result > 30  # reasonable CMRR for these dimensions


# ---------------------------------------------------------------------------
# End-to-end sizing: one_stage_opamp
# ---------------------------------------------------------------------------

@pytest.fixture(scope="module")
def one_stage_fbr():
    return _fbr("one_stage_opamp", {
        "input_pair": "differential_pair_pmos",
        "load": "active_load_nmos",
        "tail_current": "current_mirror_tail_pmos",
        "bias_gen": "diode_connected_mosfet_bias",
    })


def test_size_one_stage_opamp(one_stage_fbr):
    parsed, sr_result, fbr_result, topology = one_stage_fbr
    tech = _tech()
    spec = SizingSpec(
        vdd=5.0, vss=0.0, ibias=10e-6, cl=20e-12,
        gain_min_db=40,  # modest — one-stage gain
        output_swing_max_v=4.0,
        output_swing_min_v=1.0,
        cmrr_min_db=50,  # max achievable ≈ 57 dB at W/L=600
    )
    result = size_circuit(parsed, sr_result, fbr_result, topology, tech, spec)

    assert result.solver_status in ("OPTIMAL", "FEASIBLE")
    assert result.transistors, "Must have at least some transistors sized"
    assert result.cc_pf is None  # one-stage has no comp cap

    # All sized transistors must have W and L within tech bounds
    for ref, s in result.transistors.items():
        assert tech.width.min <= s.w_um <= tech.width.max, f"{ref}: W={s.w_um} out of bounds"
        assert tech.length.min <= s.l_um <= tech.length.max, f"{ref}: L={s.l_um} out of bounds"
        assert s.vds_sat_v > 0

    # Gain should meet spec
    if "gain_db" in result.metrics:
        assert result.metrics["gain_db"] >= spec.gain_min_db


def test_size_one_stage_input_pair_matched(one_stage_fbr):
    parsed, sr_result, fbr_result, topology = one_stage_fbr
    tech = _tech()
    spec = SizingSpec(vdd=5.0, vss=0.0, ibias=10e-6, cl=20e-12, gain_min_db=40)
    result = size_circuit(parsed, sr_result, fbr_result, topology, tech, spec)

    # m1_input_pair and m2_input_pair must be matched (equal W, equal L)
    ip = {ref: s for ref, s in result.transistors.items() if "input_pair" in ref}
    if len(ip) >= 2:
        vals = list(ip.values())
        assert vals[0].w_um == vals[1].w_um, "Input pair W must be matched"
        assert vals[0].l_um == vals[1].l_um, "Input pair L must be matched"


# ---------------------------------------------------------------------------
# End-to-end sizing: two_stage_opamp_single_ended
# ---------------------------------------------------------------------------

@pytest.fixture(scope="module")
def two_stage_fbr():
    return _fbr("two_stage_opamp_single_ended", {
        "input_pair": "differential_pair_pmos",
        "load": "active_load_nmos",
        "tail_current": "current_mirror_tail_pmos",
        "second_stage": "common_source",
        "bias_gen": "diode_connected_mosfet_bias",
        "compensation": "miller_cap",
    })


def test_size_two_stage_all_specs(two_stage_fbr):
    """Verify gain, GBW, PM, SR, power, and swing specs are jointly achievable.

    CMRR is excluded: CMRR=50 dB + GBW=2.5 MHz + SR=3.5 MV/s are mutually
    exclusive for ibias=10 µA — meeting CMRR forces Cc ≥ 20 pF, making
    SR = ibias/Cc = 497 kV/s << 3.5 MV/s.  CMRR is tested separately.
    """
    parsed, sr_result, fbr_result, topology = two_stage_fbr
    tech = _tech()
    spec = SizingSpec(
        vdd=5.0, vss=0.0, ibias=10e-6, cl=20e-12,
        second_stage_current_ratio=2.5,
        gain_min_db=80,
        gbw_min_hz=2.5e6,
        phase_margin_min_deg=60,
        slew_rate_min_vps=3.5e6,
        power_max_w=1e-3,
        output_swing_max_v=4.6,
        output_swing_min_v=0.4,
    )
    result = size_circuit(parsed, sr_result, fbr_result, topology, tech, spec)

    assert result.solver_status in ("OPTIMAL", "FEASIBLE")
    assert result.transistors
    assert result.cc_pf is not None
    assert result.cc_pf > 0

    # All W/L must be within tech bounds
    for ref, s in result.transistors.items():
        assert tech.width.min <= s.w_um <= tech.width.max
        assert tech.length.min <= s.l_um <= tech.length.max

    # Core specs must be met
    if "gain_db" in result.metrics:
        assert result.metrics["gain_db"] >= spec.gain_min_db, "Gain not met"
    if "gbw_hz" in result.metrics:
        assert result.metrics["gbw_hz"] >= spec.gbw_min_hz, "GBW not met"
    if "phase_margin_deg" in result.metrics:
        # 1° tolerance for integer-grid rounding: actual gm1 ≥ gm1_req due to ceiling,
        # which shifts the actual PM slightly below the analytical target.
        assert result.metrics["phase_margin_deg"] >= spec.phase_margin_min_deg - 1.0, "PM not met"
    if "slew_rate_vps" in result.metrics:
        assert result.metrics["slew_rate_vps"] >= spec.slew_rate_min_vps, "SR not met"


def test_size_two_stage_cc_from_sr(two_stage_fbr):
    """Cc should satisfy the slew rate: Cc ≤ iBias / SR_spec."""
    parsed, sr_result, fbr_result, topology = two_stage_fbr
    tech = _tech()
    spec = SizingSpec(
        vdd=5.0, vss=0.0, ibias=10e-6, cl=20e-12,
        slew_rate_min_vps=3.5e6, gbw_min_hz=2.5e6, phase_margin_min_deg=60,
    )
    result = size_circuit(parsed, sr_result, fbr_result, topology, tech, spec)
    assert result.cc_pf is not None
    cc_f = result.cc_pf * 1e-12
    # SR = iBias / Cc ≥ SR_spec → Cc ≤ iBias / SR_spec
    cc_max_from_sr = spec.ibias / spec.slew_rate_min_vps
    assert cc_f <= cc_max_from_sr * 1.001  # 0.1% tolerance for rounding


def test_size_two_stage_symmetry(two_stage_fbr):
    """Matched pairs within input_pair, load, tail_current must have equal W and L."""
    parsed, sr_result, fbr_result, topology = two_stage_fbr
    tech = _tech()
    spec = SizingSpec(vdd=5.0, vss=0.0, ibias=10e-6, cl=20e-12, gain_min_db=80)
    result = size_circuit(parsed, sr_result, fbr_result, topology, tech, spec)

    for prefix in ("input_pair", "load", "tail_current"):
        group = {r: s for r, s in result.transistors.items() if prefix in r}
        if len(group) >= 2:
            vals = list(group.values())
            for s in vals[1:]:
                assert s.w_um == vals[0].w_um, f"{prefix}: W mismatch"
                assert s.l_um == vals[0].l_um, f"{prefix}: L mismatch"


def test_size_two_stage_metrics_complete(two_stage_fbr):
    """All major performance metrics are reported in the result.

    SR and CMRR are specified independently (not together with full GBW)
    to avoid the infeasibility that arises when all three conflict.
    """
    parsed, sr_result, fbr_result, topology = two_stage_fbr
    tech = _tech()
    # Use gain + GBW + PM + CMRR (no SR — SR conflicts with CMRR at ibias=10µA)
    spec = SizingSpec(
        vdd=5.0, vss=0.0, ibias=10e-6, cl=20e-12,
        gain_min_db=80, gbw_min_hz=2.5e6, phase_margin_min_deg=60,
        cmrr_min_db=50,
    )
    result = size_circuit(parsed, sr_result, fbr_result, topology, tech, spec)
    assert "gain_db" in result.metrics
    assert "gbw_hz" in result.metrics
    assert "phase_margin_deg" in result.metrics
    assert "slew_rate_vps" in result.metrics  # always computed for two-stage
    assert "cmrr_db" in result.metrics


# ---------------------------------------------------------------------------
# Infeasible spec
# ---------------------------------------------------------------------------

def test_infeasible_spec(two_stage_fbr):
    """An impossibly tight spec should return INFEASIBLE."""
    parsed, sr_result, fbr_result, topology = two_stage_fbr
    tech = _tech()
    spec = SizingSpec(
        vdd=5.0, vss=0.0, ibias=10e-6, cl=20e-12,
        # Require W ≥ 1e6 µm which far exceeds the 600 µm grid max
        gain_min_db=300,  # ~10^15 linear gain — physically impossible
    )
    result = size_circuit(parsed, sr_result, fbr_result, topology, tech, spec,
                          time_limit_s=5.0)
    assert result.solver_status == "INFEASIBLE"
    assert result.transistors == {}
