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


def _active_load_two_stage_se(tech_name, vdd, gain_min, sr_min):
    """Size an active-load two-stage SE op-amp; return (netlist, result, tech, spec)."""
    mods = load_modules()
    topo = next(t for t in load_topologies() if t.name == "two_stage_opamp_single_ended")
    want = {"input_pair": "differential_pair_pmos", "load": "active_load_nmos",
            "tail_current": "current_mirror_tail_pmos", "second_stage": "common_source",
            "bias_gen": "diode_connected_mosfet_bias", "compensation": "miller_cap"}
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
    deck = spice_sim._emit_model(load_tech("generic"))
    assert "level=1" in deck and "kp=" in deck and ".model nmos" in deck


def test_emit_include_for_ptm():
    deck = spice_sim._emit_model(load_tech("ptm45"))
    assert ".include" in deck and "ptm_45nm_HP.pm" in deck


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
            "bias_gen": "diode_connected_mosfet_bias", "compensation": "miller_cap"}
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
    and a diagnostic note, instead of a bare ``n/a``."""
    from pathlib import Path

    ckt = (Path(__file__).resolve().parent.parent / "circuits"
           / "two_stage_opamp_single_ended" / "circuit_1201_flat.ckt")
    if not ckt.exists():
        pytest.skip("circuit_1201 fixture not present")
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
def test_check_bias_soundness_distinguishes_biasing_from_railed():
    """The SPICE DC verdict: a genuinely biasing design is sound; a circuit whose
    operating point rails (circuit_0010 at 1.0 V) is flagged not-sound."""
    from pathlib import Path

    topo = next(t for t in load_topologies()
                if t.name == "two_stage_opamp_single_ended")

    # genuinely biasing design (generic, 5 V) → sound
    text, result, tech, spec = _active_load_two_stage_se("generic", 5.0, 80, 3.5e6)
    ok, reason = spice_sim.check_bias_soundness(text, result, tech, spec)
    assert ok and reason is None

    # circuit_0010: output stage current-mismatched → operating point rails
    ckt = (Path(__file__).resolve().parent.parent / "circuits"
           / "two_stage_opamp_single_ended" / "circuit_0010_flat.ckt")
    if not ckt.exists():
        pytest.skip("circuit_0010 fixture not present")
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
    assert spice_sim._pm_plausible(None)
    assert spice_sim._pm_plausible(60.0)
    assert spice_sim._pm_plausible(180.0)
    assert not spice_sim._pm_plausible(0.0)
    assert not spice_sim._pm_plausible(-10.0)
    assert not spice_sim._pm_plausible(285.0)


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
            "bias_gen": "diode_connected_mosfet_bias",
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
    """A corrupt AC sweep (PM ≈ 266° from the wrong-polarity branch) must not
    be reported as a measurement: gain/GBW/PM come back None with a note."""
    text, result, tech, spec = _resistor_tail_two_stage_se(
        "differential_ota_second_stage")
    sim = spice_sim.simulate_metrics(text, result, tech, spec)
    assert sim["gain_db"] is None
    assert sim["gbw_hz"] is None
    assert sim["phase_margin_deg"] is None
    assert any("implausible" in n for n in sim.get("notes", []))


@ngspice
def test_honest_twin_measurement_unaffected():
    """The common-source twin of the regression circuit measures normally:
    positive gain and a physical phase margin."""
    text, result, tech, spec = _resistor_tail_two_stage_se("common_source")
    sim = spice_sim.simulate_metrics(text, result, tech, spec)
    assert sim["gain_db"] is not None and sim["gain_db"] > 0
    pm = sim["phase_margin_deg"]
    assert pm is not None and 0 < pm <= 180
