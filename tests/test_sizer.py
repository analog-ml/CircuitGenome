"""Tests for the Initial Sizing module (circuitgenome/sizer)."""
from __future__ import annotations
import math
import pytest

from circuitgenome.sizer import load_tech, size_circuit, SizingSpec
from circuitgenome.sizer.equations import (
    cmrr_db,
    gd,
    gm,
    gm_ceiling,
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


def test_gm_ceiling():
    # gm ceiling = gm/Id_max * |Id| (weak-inversion limit), and it caps the
    # square-law gm for an over-wide / low-current device.
    assert gm_ceiling(5e-6) == pytest.approx(25.0 * 5e-6)
    assert gm_ceiling(-5e-6) == pytest.approx(25.0 * 5e-6)  # sign-agnostic
    # An oversized device at low current: square-law gm exceeds the ceiling.
    assert gm(90e-6, 7.0, 0.045, 5e-6) > gm_ceiling(5e-6)


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


@pytest.fixture(scope="module")
def two_stage_resistor_load_fbr():
    return _fbr("two_stage_opamp_single_ended", {
        "input_pair": "differential_pair_pmos",
        "load": "resistor_load_gnd",
        "tail_current": "current_mirror_tail_pmos",
        "second_stage": "common_source",
        "bias_gen": "diode_connected_mosfet_bias",
        "compensation": "miller_cap",
    })


def test_resistor_load_is_sized_and_modeled(two_stage_resistor_load_fbr, two_stage_fbr):
    """Load resistors get a sized value and lower the modelled gain vs an
    active load (the resistor now appears in Rout1)."""
    parsed, sr, fbr, topo = two_stage_resistor_load_fbr
    tech = _tech()
    spec = SizingSpec(vdd=5.0, vss=0.0, ibias=10e-6, cl=20e-12,
                      second_stage_current_ratio=2.5, gain_min_db=40,
                      gbw_min_hz=2.5e6, phase_margin_min_deg=60, slew_rate_min_vps=3.5e6)
    r = size_circuit(parsed, sr, fbr, topo, tech, spec)
    assert r.solver_status in ("OPTIMAL", "FEASIBLE")
    # Both load resistors sized to a finite, non-placeholder value.
    assert set(r.resistors) == {"r1_load", "r2_load"}
    assert all(1e3 < ohms < 1e8 for ohms in r.resistors.values())
    # R sets V_node ≈ Vth_n + Vov at the branch current → R = V/(ibias/2).
    expected = (tech.nmos.vth + 0.15) / (spec.ibias / 2)
    assert abs(r.resistors["r1_load"] - expected) / expected < 1e-6

    # Modelling the resistor lowers gain vs the equivalent active-load circuit.
    pa, sa, fa, ta = two_stage_fbr
    ra = size_circuit(pa, sa, fa, ta, tech, spec)
    assert r.metrics["gain_db"] < ra.metrics["gain_db"]


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


def test_current_mirror_ratios_enforced(two_stage_fbr):
    """Current-mirror output W/L tracks its reference by the current ratio so the
    bias network produces the assumed currents (issue #67)."""
    parsed, sr_result, fbr_result, topology = two_stage_fbr
    tech = _tech()
    spec = SizingSpec(vdd=5.0, vss=0.0, ibias=10e-6, cl=20e-12,
                      second_stage_current_ratio=2.5, gain_min_db=80,
                      gbw_min_hz=2.5e6, phase_margin_min_deg=60, slew_rate_min_vps=3.5e6)
    result = size_circuit(parsed, sr_result, fbr_result, topology, tech, spec)
    assert result.solver_status in ("OPTIMAL", "FEASIBLE")
    t = result.transistors
    # 2nd-stage PMOS current-source load mirrors mp5_bias_gen at ratio 2.5.
    load, ref = t["mp1_second_stage"], t["mp5_bias_gen"]
    assert load.l_um == ref.l_um
    assert load.w_um == pytest.approx(2.5 * ref.w_um, rel=1e-6)
    # Tail mirror is 1:1 (output == reference).
    assert t["m2_tail_current"].w_um == pytest.approx(t["m1_tail_current"].w_um)
    assert t["m2_tail_current"].l_um == pytest.approx(t["m1_tail_current"].l_um)


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

def test_impossible_gain_flagged_not_infeasible(two_stage_fbr):
    """An impossible gain spec is sized to the achievable maximum and flagged,
    not silently passed.  The gm ceiling (issue #69) caps the gm requirement at
    the weak-inversion limit, so the solver stays feasible but the reported gain
    falls short (negative margin) and a gm-ceiling warning is emitted."""
    parsed, sr_result, fbr_result, topology = two_stage_fbr
    tech = _tech()
    spec = SizingSpec(
        vdd=5.0, vss=0.0, ibias=10e-6, cl=20e-12,
        gain_min_db=300,  # ~10^15 linear gain — physically impossible
    )
    result = size_circuit(parsed, sr_result, fbr_result, topology, tech, spec,
                          time_limit_s=5.0)
    assert result.solver_status in ("OPTIMAL", "FEASIBLE")
    # Honest shortfall: reported gain is far below spec, and it's flagged.
    assert result.metrics["gain_db"] < spec.gain_min_db
    assert result.margins["gain_db"] < 0
    assert any("weak-inversion ceiling" in w for w in result.warnings)
    assert result.transistors  # a best-effort design is still produced


# ---------------------------------------------------------------------------
# End-to-end sizing: two_stage_opamp_fully_differential
# ---------------------------------------------------------------------------

@pytest.fixture(scope="module")
def two_stage_fd_fbr():
    return _fbr("two_stage_opamp_fully_differential", {
        "input_pair":     "differential_pair_pmos",
        "load":           "folded_cascode_load_pmos_input_differential_output",
        "tail_current":   "current_mirror_tail_pmos",
        "bias_gen":       "diode_connected_mosfet_bias",
        "cmfb":           "resistive_sense_cmfb",
        "comp_p":         "miller_cap",
        "comp_n":         "miller_cap",
        "second_stage_p": "common_source",
        "second_stage_n": "common_source",
    })


def test_size_fd_basic(two_stage_fd_fbr):
    """FD two-stage: solver returns OPTIMAL/FEASIBLE with Cc and valid W/L."""
    parsed, sr_result, fbr_result, topology = two_stage_fd_fbr
    tech = _tech()
    spec = SizingSpec(
        vdd=5.0, vss=0.0, ibias=10e-6, cl=20e-12,
        second_stage_current_ratio=2.5,
        gain_min_db=80,
        gbw_min_hz=2.5e6,
        phase_margin_min_deg=60,
        slew_rate_min_vps=3.5e6,
        power_max_w=2e-3,
        output_swing_max_v=4.6,
        output_swing_min_v=0.4,
    )
    result = size_circuit(parsed, sr_result, fbr_result, topology, tech, spec)

    assert result.solver_status in ("OPTIMAL", "FEASIBLE")
    assert result.transistors
    assert result.cc_pf is not None and result.cc_pf > 0

    for ref, s in result.transistors.items():
        assert tech.width.min <= s.w_um <= tech.width.max, f"{ref}: W out of bounds"
        assert tech.length.min <= s.l_um <= tech.length.max, f"{ref}: L out of bounds"
        assert s.vds_sat_v > 0


def test_fd_specs_met(two_stage_fd_fbr):
    """FD: gain, GBW, PM, and SR all meet the spec."""
    parsed, sr_result, fbr_result, topology = two_stage_fd_fbr
    tech = _tech()
    spec = SizingSpec(
        vdd=5.0, vss=0.0, ibias=10e-6, cl=20e-12,
        second_stage_current_ratio=2.5,
        gain_min_db=80,
        gbw_min_hz=2.5e6,
        phase_margin_min_deg=60,
        slew_rate_min_vps=3.5e6,
    )
    result = size_circuit(parsed, sr_result, fbr_result, topology, tech, spec)

    assert result.solver_status in ("OPTIMAL", "FEASIBLE")
    if "gain_db" in result.metrics:
        assert result.metrics["gain_db"] >= spec.gain_min_db, "Gain not met"
    if "gbw_hz" in result.metrics:
        assert result.metrics["gbw_hz"] >= spec.gbw_min_hz, "GBW not met"
    if "phase_margin_deg" in result.metrics:
        assert result.metrics["phase_margin_deg"] >= spec.phase_margin_min_deg - 1.0, "PM not met"
    if "slew_rate_vps" in result.metrics:
        assert result.metrics["slew_rate_vps"] >= spec.slew_rate_min_vps, "SR not met"


def test_fd_second_stage_symmetry(two_stage_fd_fbr):
    """second_stage_p and second_stage_n must have equal W and L per transistor type."""
    parsed, sr_result, fbr_result, topology = two_stage_fd_fbr
    tech = _tech()
    spec = SizingSpec(
        vdd=5.0, vss=0.0, ibias=10e-6, cl=20e-12,
        second_stage_current_ratio=2.5,
        gain_min_db=80, gbw_min_hz=2.5e6, phase_margin_min_deg=60,
        slew_rate_min_vps=3.5e6,
    )
    result = size_circuit(parsed, sr_result, fbr_result, topology, tech, spec)
    assert result.solver_status in ("OPTIMAL", "FEASIBLE")

    p_devs = {r: s for r, s in result.transistors.items() if "second_stage_p" in r}
    n_devs = {r: s for r, s in result.transistors.items() if "second_stage_n" in r}
    assert p_devs and n_devs, "Both second_stage_p and second_stage_n must be sized"

    # Strip slot suffix to match corresponding devices across the two slots.
    p_bases = {r.replace("_second_stage_p", ""): s for r, s in p_devs.items()}
    n_bases = {r.replace("_second_stage_n", ""): s for r, s in n_devs.items()}
    matched = {b for b in p_bases if b in n_bases}
    assert matched, "No matching base refs found between second_stage_p and second_stage_n"
    for base in matched:
        assert p_bases[base].w_um == n_bases[base].w_um, f"{base}: W mismatch p vs n"
        assert p_bases[base].l_um == n_bases[base].l_um, f"{base}: L mismatch p vs n"


def test_fd_power_two_second_stages(two_stage_fd_fbr):
    """FD power should include current from both second-stage paths."""
    parsed, sr_result, fbr_result, topology = two_stage_fd_fbr
    tech = _tech()
    ratio = 2.5
    spec = SizingSpec(
        vdd=5.0, vss=0.0, ibias=10e-6, cl=20e-12,
        second_stage_current_ratio=ratio,
    )
    result = size_circuit(parsed, sr_result, fbr_result, topology, tech, spec)
    assert result.solver_status in ("OPTIMAL", "FEASIBLE")
    # Minimum expected power: tail (ibias) + 2 × second_stage (ids_2) on 5V supply
    ids_2 = spec.ibias * ratio
    min_expected_power = spec.vdd * (spec.ibias + 2 * ids_2)
    assert result.metrics["power_w"] >= min_expected_power * 0.9


def test_fd_cc_from_sr(two_stage_fd_fbr):
    """FD: Cc should satisfy the slew-rate constraint (Cc ≤ ibias / SR)."""
    parsed, sr_result, fbr_result, topology = two_stage_fd_fbr
    tech = _tech()
    spec = SizingSpec(
        vdd=5.0, vss=0.0, ibias=10e-6, cl=20e-12,
        slew_rate_min_vps=3.5e6, gbw_min_hz=2.5e6, phase_margin_min_deg=60,
    )
    result = size_circuit(parsed, sr_result, fbr_result, topology, tech, spec)
    assert result.cc_pf is not None
    cc_f = result.cc_pf * 1e-12
    cc_max_from_sr = spec.ibias / spec.slew_rate_min_vps
    assert cc_f <= cc_max_from_sr * 1.001


# ---------------------------------------------------------------------------
# End-to-end sizing: three-stage opamps (NMC + RNMC, SE + FD)
# ---------------------------------------------------------------------------

_THREE_STAGE_SPEC = dict(
    vdd=5.0, vss=0.0, ibias=10e-6, cl=20e-12,
    second_stage_current_ratio=2.5,
    third_stage_current_ratio=5.0,
    gain_min_db=100,
    gbw_min_hz=2.5e6,
    phase_margin_min_deg=60,
    slew_rate_min_vps=3.5e6,
)


@pytest.fixture(scope="module")
def three_stage_nmc_se_fbr():
    return _fbr("three_stage_opamp_nmc_single_ended", {
        "input_pair":   "differential_pair_pmos",
        "load":         "folded_cascode_load_pmos_input_single_output",
        "tail_current": "current_mirror_tail_pmos",
        "bias_gen":     "diode_connected_mosfet_bias",
        "second_stage": "common_source",
        "third_stage":  "common_source",
        "comp1":        "miller_cap",
        "comp2":        "miller_cap",
    })


@pytest.fixture(scope="module")
def three_stage_rnmc_se_fbr():
    return _fbr("three_stage_opamp_rnmc_single_ended", {
        "input_pair":   "differential_pair_pmos",
        "load":         "folded_cascode_load_pmos_input_single_output",
        "tail_current": "current_mirror_tail_pmos",
        "bias_gen":     "diode_connected_mosfet_bias",
        "second_stage": "common_source",
        "third_stage":  "common_source",
        "comp1":        "miller_cap",
        "comp2":        "miller_cap",
    })


@pytest.fixture(scope="module")
def three_stage_nmc_fd_fbr():
    return _fbr("three_stage_opamp_nmc_fully_differential", {
        "input_pair":      "differential_pair_pmos",
        "load":            "folded_cascode_load_pmos_input_differential_output",
        "tail_current":    "current_mirror_tail_pmos",
        "bias_gen":        "diode_connected_mosfet_bias",
        "cmfb":            "resistive_sense_cmfb",
        "second_stage_p":  "common_source",
        "second_stage_n":  "common_source",
        "third_stage_p":   "common_source",
        "third_stage_n":   "common_source",
        "comp1_p":         "miller_cap",
        "comp1_n":         "miller_cap",
        "comp2_p":         "miller_cap",
        "comp2_n":         "miller_cap",
    })


@pytest.fixture(scope="module")
def three_stage_rnmc_fd_fbr():
    return _fbr("three_stage_opamp_rnmc_fully_differential", {
        "input_pair":      "differential_pair_pmos",
        "load":            "folded_cascode_load_pmos_input_differential_output",
        "tail_current":    "current_mirror_tail_pmos",
        "bias_gen":        "diode_connected_mosfet_bias",
        "cmfb":            "resistive_sense_cmfb",
        "second_stage_p":  "common_source",
        "second_stage_n":  "common_source",
        "third_stage_p":   "common_source",
        "third_stage_n":   "common_source",
        "comp1_p":         "miller_cap",
        "comp1_n":         "miller_cap",
        "comp2_p":         "miller_cap",
        "comp2_n":         "miller_cap",
    })


# --- SE NMC ---

def test_size_three_stage_se_basic(three_stage_nmc_se_fbr):
    """Three-stage NMC SE: solver returns OPTIMAL/FEASIBLE; both caps present."""
    parsed, sr_result, fbr_result, topology = three_stage_nmc_se_fbr
    tech = _tech()
    result = size_circuit(parsed, sr_result, fbr_result, topology, tech,
                          SizingSpec(**_THREE_STAGE_SPEC))
    assert result.solver_status in ("OPTIMAL", "FEASIBLE")
    assert result.transistors
    assert result.cc_pf is not None and result.cc_pf > 0
    assert result.cc2_pf is not None and result.cc2_pf > 0
    for ref, s in result.transistors.items():
        assert tech.width.min <= s.w_um <= tech.width.max, f"{ref}: W out of bounds"
        assert tech.length.min <= s.l_um <= tech.length.max, f"{ref}: L out of bounds"


def test_three_stage_se_cc2_ratio(three_stage_nmc_se_fbr):
    """cc2_pf must equal cc_pf / 4 (Cc2 = Cc1/4 heuristic)."""
    parsed, sr_result, fbr_result, topology = three_stage_nmc_se_fbr
    tech = _tech()
    result = size_circuit(parsed, sr_result, fbr_result, topology, tech,
                          SizingSpec(**_THREE_STAGE_SPEC))
    assert result.solver_status in ("OPTIMAL", "FEASIBLE")
    assert result.cc_pf is not None and result.cc2_pf is not None
    assert result.cc2_pf == pytest.approx(result.cc_pf / 4.0, rel=1e-9)


def test_three_stage_se_specs_met(three_stage_nmc_se_fbr):
    """Three-stage NMC SE: gain, GBW, PM, and SR all meet spec."""
    parsed, sr_result, fbr_result, topology = three_stage_nmc_se_fbr
    tech = _tech()
    spec = SizingSpec(**_THREE_STAGE_SPEC)
    result = size_circuit(parsed, sr_result, fbr_result, topology, tech, spec)
    assert result.solver_status in ("OPTIMAL", "FEASIBLE")
    if "gain_db" in result.metrics:
        assert result.metrics["gain_db"] >= spec.gain_min_db, "Gain not met"
    if "gbw_hz" in result.metrics:
        assert result.metrics["gbw_hz"] >= spec.gbw_min_hz, "GBW not met"
    if "phase_margin_deg" in result.metrics:
        assert result.metrics["phase_margin_deg"] >= spec.phase_margin_min_deg - 1.0, "PM not met"
    if "slew_rate_vps" in result.metrics:
        assert result.metrics["slew_rate_vps"] >= spec.slew_rate_min_vps, "SR not met"


def test_three_stage_se_power(three_stage_nmc_se_fbr):
    """Power accounts for tail + second stage + third stage."""
    parsed, sr_result, fbr_result, topology = three_stage_nmc_se_fbr
    tech = _tech()
    spec = SizingSpec(**_THREE_STAGE_SPEC)
    result = size_circuit(parsed, sr_result, fbr_result, topology, tech, spec)
    assert result.solver_status in ("OPTIMAL", "FEASIBLE")
    ids_2 = spec.ibias * spec.second_stage_current_ratio
    ids_3 = spec.ibias * spec.third_stage_current_ratio
    min_expected = spec.vdd * (spec.ibias + ids_2 + ids_3)
    assert result.metrics["power_w"] >= min_expected * 0.9


# --- SE RNMC ---

def test_size_three_stage_rnmc_se_basic(three_stage_rnmc_se_fbr):
    """Three-stage RNMC SE: same conservative equations → OPTIMAL/FEASIBLE."""
    parsed, sr_result, fbr_result, topology = three_stage_rnmc_se_fbr
    tech = _tech()
    result = size_circuit(parsed, sr_result, fbr_result, topology, tech,
                          SizingSpec(**_THREE_STAGE_SPEC))
    assert result.solver_status in ("OPTIMAL", "FEASIBLE")
    assert result.cc_pf is not None
    assert result.cc2_pf is not None


# --- FD NMC ---

def test_size_three_stage_fd_basic(three_stage_nmc_fd_fbr):
    """Three-stage NMC FD: OPTIMAL/FEASIBLE; both caps present."""
    parsed, sr_result, fbr_result, topology = three_stage_nmc_fd_fbr
    tech = _tech()
    result = size_circuit(parsed, sr_result, fbr_result, topology, tech,
                          SizingSpec(**_THREE_STAGE_SPEC))
    assert result.solver_status in ("OPTIMAL", "FEASIBLE")
    assert result.transistors
    assert result.cc_pf is not None and result.cc_pf > 0
    assert result.cc2_pf is not None and result.cc2_pf > 0


def test_three_stage_fd_second_stage_symmetry(three_stage_nmc_fd_fbr):
    """second_stage_p and second_stage_n must have equal W and L."""
    parsed, sr_result, fbr_result, topology = three_stage_nmc_fd_fbr
    tech = _tech()
    result = size_circuit(parsed, sr_result, fbr_result, topology, tech,
                          SizingSpec(**_THREE_STAGE_SPEC))
    assert result.solver_status in ("OPTIMAL", "FEASIBLE")
    p_devs = {r.replace("_second_stage_p", ""): s
              for r, s in result.transistors.items() if "second_stage_p" in r}
    n_devs = {r.replace("_second_stage_n", ""): s
              for r, s in result.transistors.items() if "second_stage_n" in r}
    for base in p_devs:
        if base in n_devs:
            assert p_devs[base].w_um == n_devs[base].w_um, f"{base}: W mismatch"
            assert p_devs[base].l_um == n_devs[base].l_um, f"{base}: L mismatch"


def test_three_stage_fd_third_stage_symmetry(three_stage_nmc_fd_fbr):
    """third_stage_p and third_stage_n must have equal W and L."""
    parsed, sr_result, fbr_result, topology = three_stage_nmc_fd_fbr
    tech = _tech()
    result = size_circuit(parsed, sr_result, fbr_result, topology, tech,
                          SizingSpec(**_THREE_STAGE_SPEC))
    assert result.solver_status in ("OPTIMAL", "FEASIBLE")
    p_devs = {r.replace("_third_stage_p", ""): s
              for r, s in result.transistors.items() if "third_stage_p" in r}
    n_devs = {r.replace("_third_stage_n", ""): s
              for r, s in result.transistors.items() if "third_stage_n" in r}
    assert p_devs and n_devs, "Both third_stage_p and third_stage_n must be sized"
    matched = {b for b in p_devs if b in n_devs}
    assert matched
    for base in matched:
        assert p_devs[base].w_um == n_devs[base].w_um, f"{base}: W mismatch"
        assert p_devs[base].l_um == n_devs[base].l_um, f"{base}: L mismatch"


def test_three_stage_fd_power(three_stage_nmc_fd_fbr):
    """FD three-stage power accounts for 2×second + 2×third stage currents."""
    parsed, sr_result, fbr_result, topology = three_stage_nmc_fd_fbr
    tech = _tech()
    spec = SizingSpec(**_THREE_STAGE_SPEC)
    result = size_circuit(parsed, sr_result, fbr_result, topology, tech, spec)
    assert result.solver_status in ("OPTIMAL", "FEASIBLE")
    ids_2 = spec.ibias * spec.second_stage_current_ratio
    ids_3 = spec.ibias * spec.third_stage_current_ratio
    min_expected = spec.vdd * (spec.ibias + 2 * ids_2 + 2 * ids_3)
    assert result.metrics["power_w"] >= min_expected * 0.9


# --- FD RNMC ---

def test_size_three_stage_rnmc_fd_basic(three_stage_rnmc_fd_fbr):
    """Three-stage RNMC FD: OPTIMAL/FEASIBLE; both caps present."""
    parsed, sr_result, fbr_result, topology = three_stage_rnmc_fd_fbr
    tech = _tech()
    result = size_circuit(parsed, sr_result, fbr_result, topology, tech,
                          SizingSpec(**_THREE_STAGE_SPEC))
    assert result.solver_status in ("OPTIMAL", "FEASIBLE")
    assert result.cc_pf is not None
    assert result.cc2_pf is not None


# ---------------------------------------------------------------------------
# Polarity-agnostic metrics & topology-mismatch guard
# ---------------------------------------------------------------------------

def _fbr_pmos_cs_second_stage(topology_name: str):
    """Return the FBR tuple for the first variant whose second-stage signal
    transistor is a PMOS (a PMOS-common-source stage)."""
    from circuitgenome.sizer.sizer import _extract_slot_transistors, _is_signal_dev

    modules = load_modules()
    topology = next(t for t in load_topologies() if t.name == topology_name)
    for circuit in enumerate_circuits(topology, modules):
        parsed = parse(to_flat_spice(circuit))
        sr_result = recognize(parsed)
        fbr_result = assign_slots(sr_result, topology)
        slot_t = _extract_slot_transistors(fbr_result)
        ss = slot_t.get("second_stage", [])
        signal = next((d for d in ss if _is_signal_dev(d)), None)
        # Require an active (transistor) load so the high three-stage gain target
        # is achievable — resistor-load variants are intentionally gain-limited.
        if signal is not None and signal.type == "pmos" and slot_t.get("load"):
            return parsed, sr_result, fbr_result, topology
    raise AssertionError(f"no PMOS-CS second-stage variant found for {topology_name}")


def test_three_stage_pmos_cs_metrics_present():
    """PMOS-common-source stages must still report gain, PM, and PSRR+.

    Regression: metrics were previously read only from the NMOS device, so a
    PMOS-CS stage yielded gm2=gm3=0 and silently dropped these three metrics.
    """
    parsed, sr_result, fbr_result, topology = _fbr_pmos_cs_second_stage(
        "three_stage_opamp_nmc_single_ended"
    )
    result = size_circuit(parsed, sr_result, fbr_result, topology, _tech(),
                          SizingSpec(**_THREE_STAGE_SPEC))
    assert result.solver_status in ("OPTIMAL", "FEASIBLE")
    for key in ("gain_db", "phase_margin_deg", "psrr_db"):
        assert key in result.metrics, f"{key} missing for PMOS-CS stage"
        assert result.metrics[key] > 0


def test_topology_mismatch_warns():
    """Sizing a single-ended netlist against a fully-differential topology
    yields stage slots with no signal device — surface a warning, not silence."""
    _, se_circuit = _make_circuit("three_stage_opamp_nmc_single_ended")
    parsed = parse(to_flat_spice(se_circuit))
    sr_result = recognize(parsed)
    fd_topology = next(
        t for t in load_topologies()
        if t.name == "three_stage_opamp_nmc_fully_differential"
    )
    fbr_result = assign_slots(sr_result, fd_topology)
    result = size_circuit(parsed, sr_result, fbr_result, fd_topology, _tech(),
                          SizingSpec(**_THREE_STAGE_SPEC))
    assert result.warnings
    assert any("_p" in w for w in result.warnings)


# ---------------------------------------------------------------------------
# PTM technology configs (45/32/22/16 nm bulk, ngspice-extracted)
# ---------------------------------------------------------------------------

def _config_dir():
    from pathlib import Path
    import circuitgenome.sizer as _sz
    return Path(_sz.__file__).parent / "config"


# node -> nominal Vdd (V)
_PTM_NODES = {"45": 1.0, "32": 0.9, "22": 0.8, "16": 0.7}


@pytest.mark.parametrize("node,vdd", sorted(_PTM_NODES.items()))
def test_ptm_tech_loads_and_sizes(two_stage_fbr, node, vdd):
    """Each PTM tech config parses and yields a feasible node-appropriate sizing."""
    tech = load_tech(_config_dir() / f"tech_ptm{node}.yaml")
    # sanity on parsed params: NMOS µCox > PMOS µCox > 0; |Vth| reasonable; λ > 0
    assert tech.nmos.mu_cox > tech.pmos.mu_cox > 0
    assert 0.2 < tech.nmos.vth < 0.6 and -0.6 < tech.pmos.vth < -0.2
    assert tech.nmos.lam > 0 and tech.pmos.lam > 0

    parsed, sr_result, fbr_result, topology = two_stage_fbr
    spec = SizingSpec(
        vdd=vdd, vss=0.0, ibias=10e-6, cl=1e-12,
        second_stage_current_ratio=2.5,
        gain_min_db=40, gbw_min_hz=2.5e6,
        phase_margin_min_deg=60, slew_rate_min_vps=1e6,
    )
    result = size_circuit(parsed, sr_result, fbr_result, topology, tech, spec)
    # ptm45 carries a gm/Id LUT → procedural "GMID" path; the others fall back
    # to the Level-1 CP-SAT solver.
    expected = ("GMID",) if tech.gmid_lut else ("OPTIMAL", "FEASIBLE")
    assert result.solver_status in expected
    assert result.transistors
    # every device sits inside the node's W/L grid
    for s in result.transistors.values():
        assert tech.width.min <= s.w_um <= tech.width.max
        assert tech.length.min <= s.l_um <= tech.length.max


def test_first_stage_gain_factor():
    """k_fs is 1.0 for a current-mirror/FD first stage, 0.5 for non-mirror SE."""
    from circuitgenome.sizer.sizer import _first_stage_gain_factor
    from circuitgenome.synthesizer.models import Device

    mirror = {"load": [
        Device(ref="m1_load", type="nmos", terminals={"g": "x", "d": "x", "s": "0"}),
        Device(ref="m2_load", type="nmos", terminals={"g": "x", "d": "y", "s": "0"}),
    ]}
    current_source = {"load": [
        Device(ref="m1_load", type="nmos", terminals={"g": "net_bias1", "d": "y", "s": "0"}),
    ]}
    resistor = {"load": []}  # resistor load has no load MOSFETs
    fully_diff = {"second_stage_p": [], "load": []}

    assert _first_stage_gain_factor(mirror) == 1.0
    assert _first_stage_gain_factor(current_source) == 0.5
    assert _first_stage_gain_factor(resistor) == 0.5
    assert _first_stage_gain_factor(fully_diff) == 1.0


def test_ptm45_uses_gmid_path_and_matches_pairs(two_stage_fbr):
    """ptm45 routes through the procedural gm/Id sizer with matched input pair."""
    tech = load_tech("ptm45")
    assert tech.gmid_lut  # LUT present → gm/Id path
    parsed, sr_result, fbr_result, topology = two_stage_fbr
    spec = SizingSpec(
        vdd=1.0, vss=0.0, ibias=20e-6, cl=2e-12,
        second_stage_current_ratio=2.5,
        gain_min_db=50, gbw_min_hz=5e6, phase_margin_min_deg=60,
    )
    result = size_circuit(parsed, sr_result, fbr_result, topology, tech, spec)
    assert result.solver_status == "GMID"
    ip = sorted(r for r in result.transistors if "input_pair" in r)
    assert len(ip) == 2
    a, b = (result.transistors[r] for r in ip)
    assert a.w_um == b.w_um and a.l_um == b.l_um  # matched differential pair


# ---------------------------------------------------------------------------
# PTM example-spec feasibility (issue #74): every committed spec must size
# without a gm-ceiling shortfall and with all margins met on its topology's
# active-load reference circuit, so they can't silently drift to "not met".
# ---------------------------------------------------------------------------
import functools  # noqa: E402

_SPEC_REF = {
    "one_stage_specs": ("one_stage_opamp", {
        "input_pair": "differential_pair_pmos", "load": "active_load_nmos",
        "tail_current": "current_mirror_tail_pmos",
        "bias_gen": "diode_connected_mosfet_bias"}),
    "two_stage_se_specs": ("two_stage_opamp_single_ended", {
        "input_pair": "differential_pair_pmos", "load": "active_load_nmos",
        "tail_current": "current_mirror_tail_pmos", "second_stage": "common_source",
        "bias_gen": "diode_connected_mosfet_bias", "compensation": "miller_cap"}),
    "two_stage_fd_specs": ("two_stage_opamp_fully_differential", {
        "input_pair": "differential_pair_pmos",
        "load": "folded_cascode_load_pmos_input_differential_output",
        "tail_current": "current_mirror_tail_pmos",
        "bias_gen": "diode_connected_mosfet_bias", "cmfb": "resistive_sense_cmfb",
        "comp_p": "miller_cap", "comp_n": "miller_cap",
        "second_stage_p": "common_source", "second_stage_n": "common_source"}),
    "three_stage_se_specs": ("three_stage_opamp_nmc_single_ended", {
        "input_pair": "differential_pair_pmos",
        "load": "folded_cascode_load_pmos_input_single_output",
        "tail_current": "current_mirror_tail_pmos",
        "bias_gen": "diode_connected_mosfet_bias", "second_stage": "common_source",
        "third_stage": "common_source", "comp1": "miller_cap", "comp2": "miller_cap"}),
    "three_stage_fd_specs": ("three_stage_opamp_nmc_fully_differential", {
        "input_pair": "differential_pair_pmos",
        "load": "folded_cascode_load_pmos_input_differential_output",
        "tail_current": "current_mirror_tail_pmos",
        "bias_gen": "diode_connected_mosfet_bias", "cmfb": "resistive_sense_cmfb",
        "comp1_p": "miller_cap", "comp2_p": "miller_cap",
        "comp1_n": "miller_cap", "comp2_n": "miller_cap",
        "second_stage_p": "common_source", "second_stage_n": "common_source",
        "third_stage_p": "common_source", "third_stage_n": "common_source"}),
}


@functools.lru_cache(maxsize=None)
def _ref_circuit(sdir):
    topo_name, vf = _SPEC_REF[sdir]
    return _fbr(topo_name, vf)


@pytest.mark.parametrize("node", ["ptm45", "ptm32", "ptm22", "ptm16"])
@pytest.mark.parametrize("sdir", list(_SPEC_REF))
def test_ptm_example_specs_feasible(sdir, node):
    import yaml
    from pathlib import Path

    parsed, sr_result, fbr_result, topology = _ref_circuit(sdir)
    spec_path = (Path(__file__).resolve().parent.parent / "examples"
                 / sdir / f"spec_{node}.yaml")
    data = yaml.safe_load(spec_path.read_text())
    spec = SizingSpec(**{k: v for k, v in data.items()
                         if k in SizingSpec.__dataclass_fields__})
    result = size_circuit(parsed, sr_result, fbr_result, topology,
                          load_tech(node), spec)

    assert result.transistors, f"{sdir}/{node}: no sizing"
    assert result.solver_status in ("OPTIMAL", "FEASIBLE", "GMID")
    # No weak-inversion gm-ceiling shortfall (the #74 failure mode).
    assert not any("ceiling" in w for w in result.warnings), \
        f"{sdir}/{node}: gm-ceiling shortfall: {result.warnings}"
    # ptm45 (gm/Id) may carry the documented tail-headroom advisory at 1.0 V;
    # the Level-1 nodes must be warning-clean.
    if node != "ptm45":
        assert not any("headroom" in w for w in result.warnings), \
            f"{sdir}/{node}: unexpected headroom warning: {result.warnings}"
    # Every constrained spec is met (1° tolerance on PM for grid rounding).
    for key, val in result.margins.items():
        tol = -1.0 if key == "phase_margin_deg" else 0.0
        assert val >= tol, f"{sdir}/{node}: margin {key}={val:.3f} < {tol}"
