"""Tests for the ngspice metric-verification path (circuitgenome/sizer/spice_sim).

The simulation tests are skipped when ngspice is not on PATH.
"""
from __future__ import annotations

import pytest

from circuitgenome.recognizer import parse, recognize
from circuitgenome.recognizer.functional_block_recognizer import assign_slots
from circuitgenome.synthesizer.loader import load_topologies, load_modules
from circuitgenome.synthesizer.synthesizer import enumerate_circuits
from circuitgenome.synthesizer.netlist import to_flat_spice
from circuitgenome.sizer import load_tech, size_circuit, SizingSpec
from circuitgenome.sizer.shared import spice_sim
from circuitgenome.sizer.shared.spice import deck, measure, rig


def _active_load_two_stage_se(tech_name, vdd, gain_min, sr_min):
    """Size an active-load two-stage SE op-amp; return (netlist, result, tech, spec)."""
    mods = load_modules()
    topo = next(t for t in load_topologies() if t.name == "two_stage_opamp_single_ended")
    want = {"input_pair": "differential_pair_pmos", "load": "active_load_nmos",
            "tail_current": "current_mirror_tail_pmos", "second_stage": "common_source",
            "compensation": "miller_cap"}
    circ = next(c for c in enumerate_circuits(topo, mods)
                if all(c.variant_map.get(k).name == v for k, v in want.items()))
    text = to_flat_spice(circ, name="dut")
    parsed = parse(text)
    fbr = assign_slots(recognize(parsed), topo)
    tech = load_tech(tech_name)
    spec = SizingSpec(vdd=vdd, vss=0.0, ibias=10e-6, cl=20e-12,
                      second_stage_current_ratio=2.5, gain_min_db=gain_min,
                      gbw_min_hz=2.5e6, phase_margin_min_deg=60, slew_rate_min_vps=sr_min)
    result = size_circuit(parsed, recognize(parsed), fbr, topo, tech, spec)
    return text, result, tech, spec


# --- model emission (no ngspice needed) ------------------------------------

def test_emit_level1_for_generic():
    model = deck._emit_model(load_tech("generic"))
    assert "level=1" in model and "kp=" in model and ".model nmos" in model


def test_emit_include_for_ptm():
    model = deck._emit_model(load_tech("ptm45"))
    assert ".include" in model and "ptm_45nm_HP.pm" in model


# --- bias-current direction (no ngspice needed) -----------------------------

def test_iref_direction_follows_reference_diode():
    """The rig injects into an NMOS-referenced ``ibias`` pin, but pulls the
    current out of a PMOS-referenced one (whose diode conducts from VDD out of
    the pin); no reference diode keeps the historical inject direction."""
    nmos_ref = ["mn1_bias_gen ibias ibias gnd! gnd! nmos"]
    pmos_ref = ["mp1_bias_gen ibias ibias vdd! vdd! pmos"]
    pdk_pmos_ref = ["xp1_bias_gen ibias ibias vdd! vdd! pmos_3p3 w=1u l=1u"]
    assert rig._iref_sink(nmos_ref) is False
    assert rig._iref_sink(pmos_ref) is True
    assert rig._iref_sink(pdk_pmos_ref) is True
    assert rig._iref_sink(["r1_load net_mid gnd! 1k"]) is False
    assert "Iref 0 ibias" in rig._rig(3.3, 2e-5)
    assert "Iref ibias 0" in rig._rig(3.3, 2e-5, sink=True)


def test_deck_sinks_iref_for_pmos_referenced_dut():
    """_deck adapts the Iref direction to the DUT block it instantiates."""
    ports = ["ibias", "vdd!", "gnd!"]
    netmap = {"ibias": "ibias", "vdd!": "vdd", "gnd!": "0"}
    for diode, expect in (("mp1 ibias ibias vdd! vdd! pmos", "Iref ibias 0"),
                          ("mn1 ibias ibias gnd! gnd! nmos", "Iref 0 ibias")):
        body_dut = f"* t\n.subckt dut __PORTS__\n{diode}\n.ends\n"
        d = rig._deck("dut", ports, body_dut, 3.3, 2e-5, "", netmap, "op")
        assert expect in d


# --- simulation (requires ngspice) -----------------------------------------

ngspice = pytest.mark.skipif(not spice_sim.ngspice_available(),
                             reason="ngspice not installed")


@ngspice
def test_generic_level1_tracks_analytical():
    """Level-1 SPICE should roughly track the (Level-1) analytical formulas."""
    text, result, tech, spec = _active_load_two_stage_se("generic", 5.0, 80, 3.5e6)
    assert result.solver_status in ("OPTIMAL", "FEASIBLE")
    sim = spice_sim.simulate_metrics(text, result, tech, spec)
    assert sim["power_w"] is not None and sim["power_w"] > 0
    # gain within ~20 dB and GBW within ~2x — loose, just a sanity envelope.
    assert sim["gain_db"] is not None
    assert abs(sim["gain_db"] - result.metrics["gain_db"]) < 20
    assert sim["gbw_hz"] is not None
    assert 0.3 < sim["gbw_hz"] / result.metrics["gbw_hz"] < 3.0


@ngspice
def test_ptm_bsim4_runs():
    """The BSIM4 path runs and returns a (much lower) real-device gain."""
    text, result, tech, spec = _active_load_two_stage_se("ptm45", 1.0, 60, 5e5)
    sim = spice_sim.simulate_metrics(text, result, tech, spec)
    assert sim["power_w"] is not None
    # Real 45nm gain is well below the optimistic Level-1 prediction.
    assert sim["gain_db"] is None or sim["gain_db"] < result.metrics["gain_db"]


@ngspice
def test_resistor_load_biases_in_spice():
    """With the load resistor sized, a resistor-load circuit biases correctly so
    SPICE can measure its open-loop gain (was n/a with the 1k placeholder)."""
    mods = load_modules()
    topo = next(t for t in load_topologies() if t.name == "two_stage_opamp_single_ended")
    want = {"input_pair": "differential_pair_pmos", "load": "resistor_load_gnd",
            "tail_current": "current_mirror_tail_pmos", "second_stage": "common_source",
            "compensation": "miller_cap"}
    circ = next(c for c in enumerate_circuits(topo, mods)
                if all(c.variant_map.get(k).name == v for k, v in want.items()))
    text = to_flat_spice(circ, name="dut")
    parsed = parse(text)
    fbr = assign_slots(recognize(parsed), topo)
    tech = load_tech("generic")
    spec = SizingSpec(vdd=5.0, vss=0.0, ibias=10e-6, cl=20e-12,
                      second_stage_current_ratio=2.5, gain_min_db=40,
                      gbw_min_hz=2.5e6, phase_margin_min_deg=60, slew_rate_min_vps=3.5e6)
    result = size_circuit(parsed, recognize(parsed), fbr, topo, tech, spec)
    assert result.resistors  # load resistors were sized
    sim = spice_sim.simulate_metrics(text, result, tech, spec)
    assert sim["gain_db"] is not None  # circuit biases → AC measurable


@ngspice
def test_misbiased_circuit_reports_measured_gain_and_reason():
    """A circuit that can't bias at low supply reports its measured (≤ 0 dB) gain
    and a diagnostic note, instead of a bare ``n/a``.

    circuit_0244: diode-connected (NMOS-referenced) bias, folded-cascode load —
    at 1.0 V it settles but does not amplify.  (The previous fixture,
    circuit_1201, had a PMOS-referenced bias generator that only produced this
    outcome because the rig drove its ``ibias`` pin backwards; with the
    direction-aware rig it rails honestly instead.)"""
    from pathlib import Path

    ckt = (Path(__file__).resolve().parent.parent / "circuits"
           / "two_stage_opamp_single_ended" / "circuit_0244_flat.ckt")
    if not ckt.exists():
        pytest.skip("circuit_0244 fixture not present")
    text = ckt.read_text()
    parsed = parse(text)
    topo = next(t for t in load_topologies()
                if t.name == "two_stage_opamp_single_ended")
    fbr = assign_slots(recognize(parsed), topo)
    tech = load_tech("ptm45")
    spec = SizingSpec(vdd=1.0, vss=0.0, ibias=10e-6, cl=2e-12,
                      second_stage_current_ratio=2.5, gain_min_db=60,
                      gbw_min_hz=2.5e6, phase_margin_min_deg=60, slew_rate_min_vps=5e5)
    result = size_circuit(parsed, recognize(parsed), fbr, topo, tech, spec)
    sim = spice_sim.simulate_metrics(text, result, tech, spec)

    # The folded-cascode stage can't bias at 1.0 V → measured gain ≤ 0 dB
    # (reported, not n/a); GBW/PM remain n/a; notes explain why.
    assert sim["gain_db"] is not None and sim["gain_db"] <= 0
    assert sim["gbw_hz"] is None and sim["phase_margin_deg"] is None
    notes = sim.get("notes")
    assert notes and any("amplify" in n for n in notes)
    assert any("triode" in n or "starved" in n for n in notes)


@ngspice
def test_gnd_referenced_legs_bias_in_spice():
    """A consumer set demanding gnd-referenced and current-source legs (the
    shapes the retired magic_battery_bias used to cover) biases soundly with
    the constructed generator's pref branch. (The rig's ibias direction
    handling for PMOS-referenced pins -- external netlists -- is covered by
    test_iref_direction_follows_reference_diode.)"""
    mods = load_modules()
    topo = next(t for t in load_topologies() if t.name == "two_stage_opamp_single_ended")
    want = {"input_pair": "differential_pair_nmos", "load": "active_load_pmos",
            "tail_current": "current_mirror_tail_nmos", "second_stage": "common_drain_nmos",
            "compensation": "miller_cap"}
    circ = next(c for c in enumerate_circuits(topo, mods)
                if all(c.variant_map.get(k).name == v for k, v in want.items()))
    text = to_flat_spice(circ, name="dut")
    parsed = parse(text)
    fbr = assign_slots(recognize(parsed), topo)
    tech = load_tech("gf180mcu")
    spec = SizingSpec(vdd=3.3, vss=0.0, ibias=20e-6, cl=5e-12,
                      second_stage_current_ratio=2.5, gain_min_db=60,
                      gbw_min_hz=2e6, phase_margin_min_deg=60, slew_rate_min_vps=3e5)
    result = size_circuit(parsed, recognize(parsed), fbr, topo, tech, spec)
    assert result.solver_status == "GMID"
    ok, reason = spice_sim.check_bias_soundness(text, result, tech, spec)
    assert ok and reason is None


@ngspice
def test_check_bias_soundness_distinguishes_biasing_from_railed():
    """The SPICE DC verdict: a genuinely biasing design is sound; a circuit whose
    operating point rails (circuit_0019 at 1.0 V) is flagged not-sound.
    (circuit_0010, the previous specimen, biases fine since the issue-#108 Cc /
    headroom-repair fixes.)"""
    from pathlib import Path

    topo = next(t for t in load_topologies()
                if t.name == "two_stage_opamp_single_ended")

    # genuinely biasing design (generic, 5 V) → sound
    text, result, tech, spec = _active_load_two_stage_se("generic", 5.0, 80, 3.5e6)
    ok, reason = spice_sim.check_bias_soundness(text, result, tech, spec)
    assert ok and reason is None

    # circuit_0019: output stage current-mismatched → operating point rails
    ckt = (Path(__file__).resolve().parent.parent / "circuits"
           / "two_stage_opamp_single_ended" / "circuit_0019_flat.ckt")
    if not ckt.exists():
        pytest.skip("circuit_0019 fixture not present")
    text = ckt.read_text()
    parsed = parse(text)
    fbr = assign_slots(recognize(parsed), topo)
    spec = SizingSpec(vdd=1.0, vss=0.0, ibias=10e-6, cl=2e-12,
                      second_stage_current_ratio=2.5, gain_min_db=60, gbw_min_hz=2.5e6,
                      phase_margin_min_deg=60, slew_rate_min_vps=5e5)
    tech = load_tech("ptm45")
    result = size_circuit(parsed, recognize(parsed), fbr, topo, tech, spec)
    ok, reason = spice_sim.check_bias_soundness(text, result, tech, spec)
    assert not ok and reason and "SPICE bias" in reason


# --- phase-margin plausibility guard ----------------------------------------

def test_pm_plausible_range():
    """PM is physical only in (0°, 180°]; None (no crossing) is not evidence."""
    assert measure._pm_plausible(None)
    assert measure._pm_plausible(60.0)
    assert measure._pm_plausible(180.0)
    assert not measure._pm_plausible(0.0)
    assert not measure._pm_plausible(-10.0)
    assert not measure._pm_plausible(285.0)


def _resistor_tail_two_stage_se(second_stage):
    """Size a gf180 resistor-load/resistor-tail two-stage; return sim inputs.

    With ``second_stage="differential_ota_second_stage"`` only the corrupted
    AC polarity settles in the rig (PM extracts at ~266°) — the regression
    case for the plausibility guard.  ``"common_source"`` is its honest twin.
    """
    mods = load_modules()
    topo = next(t for t in load_topologies()
                if t.name == "two_stage_opamp_single_ended")
    want = {"input_pair": "differential_pair_pmos", "load": "resistor_load_gnd",
            "tail_current": "resistor_tail_vdd",
            "compensation": "miller_cap", "second_stage": second_stage}
    circ = next(c for c in enumerate_circuits(topo, mods)
                if all(c.variant_map.get(k) and c.variant_map[k].name == v
                       for k, v in want.items()))
    text = to_flat_spice(circ, name="dut")
    parsed = parse(text)
    fbr = assign_slots(recognize(parsed), topo)
    tech = load_tech("gf180mcu")
    spec = SizingSpec(vdd=3.3, vss=0.0, ibias=20e-6, cl=5e-12,
                      second_stage_current_ratio=2.5, gain_min_db=40)
    result = size_circuit(parsed, recognize(parsed), fbr, topo, tech, spec)
    return text, result, tech, spec


@ngspice
def test_implausible_pm_extraction_is_discarded():
    """A PM above 180° (phase lead on falling gain — the right-half-plane
    pole of Miller compensation around the non-inverting two-CS second stage)
    must not be reported as a measurement: gain/GBW/PM come back None with a
    note naming the unsound response."""
    text, result, tech, spec = _resistor_tail_two_stage_se(
        "differential_ota_second_stage")
    sim = spice_sim.simulate_metrics(text, result, tech, spec)
    assert sim["gain_db"] is None
    assert sim["gbw_hz"] is None
    assert sim["phase_margin_deg"] is None
    assert any("right-half-plane" in n or "implausible" in n
               for n in sim.get("notes", []))


@ngspice
def test_honest_twin_measurement_unaffected():
    """The common-source twin of the regression circuit measures normally:
    positive gain and a physical phase margin."""
    text, result, tech, spec = _resistor_tail_two_stage_se("common_source")
    sim = spice_sim.simulate_metrics(text, result, tech, spec)
    assert sim["gain_db"] is not None and sim["gain_db"] > 0
    pm = sim["phase_margin_deg"]
    assert pm is not None and 0 < pm <= 180


# --- CMRR / PSRR / output swing / two-edge slew -----------------------------

@ngspice
def test_new_metrics_measured_on_generic_two_stage():
    """The generic 5 V two-stage measures all four new metrics with physically
    plausible values: CMRR/PSRR well above the gain floor, output swing inside
    the rails straddling mid-supply, slew rate near the analytical ibias/Cc."""
    text, result, tech, spec = _active_load_two_stage_se("generic", 5.0, 80, 3.5e6)
    sim = spice_sim.simulate_metrics(text, result, tech, spec)

    assert sim["cmrr_db"] is not None and 20.0 < sim["cmrr_db"] < 200.0
    assert sim["psrr_db"] is not None and 20.0 < sim["psrr_db"] < 200.0

    hi, lo = sim["output_swing_max_v"], sim["output_swing_min_v"]
    assert hi is not None and lo is not None
    assert 0.0 <= lo < 2.5 < hi <= 5.0

    # min(rising, falling) slew: positive and within an order of magnitude of
    # the analytical internal limit ibias/Cc.
    sr = sim["slew_rate_vps"]
    assert sr is not None and sr > 0
    sr_analytic = spec.ibias / (result.cc_pf * 1e-12)
    assert 0.1 * sr_analytic < sr < 10.0 * sr_analytic


@ngspice
def test_cmrr_psrr_none_without_clean_gain():
    """CMRR/PSRR are ratios against the differential gain: a circuit whose AC
    measurement is not a clean positive gain must report them as None (a
    non-amplifying circuit once measured 'CMRR 242 dB' from numerical noise).

    circuit_0244 measures a ≤ 0 dB gain at 1.0 V (see
    test_misbiased_circuit_reports_measured_gain_and_reason)."""
    from pathlib import Path

    ckt = (Path(__file__).resolve().parent.parent / "circuits"
           / "two_stage_opamp_single_ended" / "circuit_0244_flat.ckt")
    if not ckt.exists():
        pytest.skip("circuit_0244 fixture not present")
    text = ckt.read_text()
    parsed = parse(text)
    topo = next(t for t in load_topologies()
                if t.name == "two_stage_opamp_single_ended")
    fbr = assign_slots(recognize(parsed), topo)
    tech = load_tech("ptm45")
    spec = SizingSpec(vdd=1.0, vss=0.0, ibias=10e-6, cl=2e-12,
                      second_stage_current_ratio=2.5, gain_min_db=60,
                      gbw_min_hz=2.5e6, phase_margin_min_deg=60, slew_rate_min_vps=5e5)
    result = size_circuit(parsed, recognize(parsed), fbr, topo, tech, spec)
    sim = spice_sim.simulate_metrics(text, result, tech, spec)

    assert sim["gain_db"] is not None and sim["gain_db"] <= 0
    assert sim["cmrr_db"] is None
    assert sim["psrr_db"] is None


@ngspice
def test_fd_large_signal_metrics_stay_none():
    """Swing and slew are single-ended-only benches: a fully-differential
    circuit keeps them (and, absent a clean FD gain, CMRR/PSRR) as None."""
    mods = load_modules()
    topo = next(t for t in load_topologies()
                if t.name == "two_stage_opamp_fully_differential")
    # mixed bias-rail flavors (rail 1/4 gnd, rail 5/7 vdd): only resistor_bias
    # survives the flavor filter (bias_compatibility.py)
    want = {"input_pair": "differential_pair_pmos",
            "load": "folded_cascode_load_pmos_input_differential_output",
            "tail_current": "current_mirror_tail_pmos",
            "comp_p": "miller_cap", "comp_n": "miller_cap",
            "second_stage_p": "common_source", "second_stage_n": "common_source",
            "cmfb": "resistive_sense_cmfb"}
    circ = next(c for c in enumerate_circuits(topo, mods)
                if all(c.variant_map.get(k) and c.variant_map[k].name == v
                       for k, v in want.items()))
    text = to_flat_spice(circ, name="dut")
    parsed = parse(text)
    fbr = assign_slots(recognize(parsed), topo)
    tech = load_tech("ptm45")
    spec = SizingSpec(vdd=1.0, vss=0.0, ibias=15e-6, cl=2e-12,
                      second_stage_current_ratio=2.5, gain_min_db=50,
                      gbw_min_hz=2e6, phase_margin_min_deg=60, slew_rate_min_vps=1e6)
    result = size_circuit(parsed, recognize(parsed), fbr, topo, tech, spec)
    sim = spice_sim.simulate_metrics(text, result, tech, spec)

    assert sim["slew_rate_vps"] is None
    assert sim["output_swing_max_v"] is None
    assert sim["output_swing_min_v"] is None
