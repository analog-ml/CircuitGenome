"""ngspice characterization gates for experimental analog blocks.

These tests cover the nominal DC operating point of the parked rail-to-rail
input and fixed-bias Class-AB output macros.  Structural and sizing tests prove
that the blocks can be assembled and dimensioned; this file additionally
guards their first electrical acceptance gate.  Full input-common-mode and
large-signal/load sweeps are still required before default-pool promotion.
"""
from __future__ import annotations

import pytest

from circuitgenome.recognizer import parse, recognize
from circuitgenome.recognizer.functional_block_recognizer import assign_slots
from circuitgenome.sizer import (
    SizingSpec,
    calibrate_class_ab_iq,
    characterize_class_ab_linearity,
    characterize_class_ab_loads,
    check_bias_soundness,
    load_tech,
    ngspice_available,
    size_circuit,
    sweep_class_ab_pvt,
    sweep_rail_to_rail_vcm,
)
from circuitgenome.sizer.verify import read_op_operating_point
from circuitgenome.synthesizer.loader import load_modules, load_topologies
from circuitgenome.synthesizer.netlist import to_flat_spice
from circuitgenome.synthesizer.synthesizer import enumerate_circuits


ngspice = pytest.mark.skipif(
    not ngspice_available(), reason="ngspice not installed"
)


def _size_ptm45(topology_name: str, variants: dict[str, str], spec: SizingSpec):
    modules = load_modules()
    topology = next(
        t for t in load_topologies() if t.name == topology_name
    )
    circuit = next(
        c for c in enumerate_circuits(
            topology, modules, {"include_unsupported": True}
        )
        if all(
            c.variant_map.get(slot)
            and c.variant_map[slot].name == variant
            for slot, variant in variants.items()
        )
    )
    netlist = to_flat_spice(circuit, name="dut")
    parsed = parse(netlist)
    recognized = recognize(parsed)
    slots = assign_slots(recognized, topology)
    tech = load_tech("ptm45")
    result = size_circuit(parsed, recognized, slots, topology, tech, spec)
    assert result.solver_status == "GMID"
    return netlist, result, tech


@ngspice
def test_rail_to_rail_nominal_midpoint_biases_in_spice():
    """Both complementary pairs and their local tails conduct at midpoint."""
    spec = SizingSpec(
        vdd=1.0,
        vss=0.0,
        ibias=20e-6,
        cl=2e-12,
        gain_min_db=20,
        gbw_min_hz=2e6,
    )
    netlist, result, tech = _size_ptm45(
        "one_stage_opamp",
        {
            "input_pair": "rail_to_rail_complementary_input",
            "load": "rail_to_rail_load_absent",
            "tail_current": "current_mirror_tail_pmos",
        },
        spec,
    )

    sound, reason = check_bias_soundness(netlist, result, tech, spec)

    assert sound, reason
    op = read_op_operating_point(netlist, result, tech, spec)
    assert op is not None
    for ref in ("mntail_input_pair", "mptail_input_pair"):
        assert abs(op[ref]["id"]) > 0.5 * spec.ibias


@ngspice
def test_rail_to_rail_input_pairs_hand_off_across_vcm():
    """One pair remains active at each rail and both conduct near mid-supply."""
    spec = SizingSpec(
        vdd=1.0, vss=0.0, ibias=20e-6, cl=2e-12,
        gain_min_db=20, gbw_min_hz=2e6,
    )
    netlist, result, tech = _size_ptm45(
        "one_stage_opamp",
        {
            "input_pair": "rail_to_rail_complementary_input",
            "load": "rail_to_rail_load_absent",
            "tail_current": "current_mirror_tail_pmos",
        },
        spec,
    )
    points = sweep_rail_to_rail_vcm(
        netlist, result, tech, spec,
        vcm_values=(0.05, 0.25, 0.50, 0.75, 0.95),
    )

    assert all(p.output_v is not None and p.gm_total_s is not None for p in points)
    low, _low_mid, middle, high_mid, high = points
    assert low.p_pair_current_a > 0.75 * spec.ibias
    assert high.n_pair_current_a > 0.75 * spec.ibias
    assert middle.n_pair_current_a > 0.5 * spec.ibias
    assert middle.p_pair_current_a > 0.5 * spec.ibias
    assert [p.n_pair_current_a for p in points] == sorted(
        p.n_pair_current_a for p in points
    )
    assert [p.p_pair_current_a for p in points] == sorted(
        (p.p_pair_current_a for p in points), reverse=True
    )
    # This basic topology has an expected mid-rail gm bump, but no dead zone.
    gms = [p.gm_total_s for p in points]
    assert min(gms) > 0
    assert max(gms) / min(gms) < 4.0
    # The central operating range also has enough gain to close the level-shifted loop.
    assert middle.converged and high_mid.converged


@ngspice
def test_class_ab_nominal_quiescent_point_biases_in_spice():
    """Both output devices conduct a finite, matched quiescent current."""
    spec = SizingSpec(
        vdd=1.0,
        vss=0.0,
        ibias=20e-6,
        cl=2e-12,
        second_stage_current_ratio=2.5,
        output_stage_current_ratio=1.5,
        gain_min_db=35,
        gbw_min_hz=3e6,
        phase_margin_min_deg=60,
    )
    netlist, result, tech = _size_ptm45(
        "two_stage_opamp_class_ab_single_ended",
        {
            "input_pair": "differential_pair_pmos",
            "load": "active_load_nmos",
            "tail_current": "current_mirror_tail_pmos",
            "compensation": "miller_cap",
            "class_ab_stage": "complementary_class_ab_output",
        },
        spec,
    )

    calibration = calibrate_class_ab_iq(netlist, result, tech, spec)
    assert calibration.converged
    assert calibration.iterations <= 3
    result = calibration.sizing

    sound, reason = check_bias_soundness(netlist, result, tech, spec)

    assert sound, reason
    op = read_op_operating_point(netlist, result, tech, spec)
    assert op is not None
    i_n = abs(op["mn_out_class_ab_stage"]["id"])
    i_p = abs(op["mp_out_class_ab_stage"]["id"])
    target = spec.ibias * spec.output_stage_current_ratio
    assert 0.25 * target < i_n < 2.0 * target
    assert 0.25 * target < i_p < 2.0 * target
    assert (i_n + i_p) / 2 == pytest.approx(target, rel=0.10)
    assert i_n == pytest.approx(i_p, rel=0.05)
    assert max(
        sizing.w_um for ref, sizing in result.transistors.items()
        if ref.endswith("_class_ab_stage")
    ) < tech.width.max


@ngspice
def test_class_ab_bidirectional_drive_and_loaded_stability_are_exposed():
    """Nominal load passes while the heavy-load PM limit remains visible."""
    spec = SizingSpec(
        vdd=1.0,
        vss=0.0,
        ibias=20e-6,
        cl=2e-12,
        second_stage_current_ratio=2.5,
        output_stage_current_ratio=1.5,
        gain_min_db=35,
        gbw_min_hz=3e6,
        phase_margin_min_deg=60,
    )
    netlist, result, tech = _size_ptm45(
        "two_stage_opamp_class_ab_single_ended",
        {
            "input_pair": "differential_pair_pmos",
            "load": "active_load_nmos",
            "tail_current": "current_mirror_tail_pmos",
            "compensation": "miller_cap",
            "class_ab_stage": "complementary_class_ab_output",
        },
        spec,
    )
    calibration = calibrate_class_ab_iq(netlist, result, tech, spec)
    assert calibration.converged

    points = characterize_class_ab_loads(
        netlist, calibration.sizing, tech, spec,
        load_cap_values=(0.5e-12, 2e-12, 10e-12),
    )

    assert len(points) == 3
    assert all(point.rise_tracking_fraction is not None for point in points)
    assert all(point.fall_tracking_fraction is not None for point in points)
    assert all(point.source_peak_a and point.source_peak_a > 0 for point in points)
    assert all(point.sink_peak_a and point.sink_peak_a > 0 for point in points)
    assert all(point.settled for point in points)
    assert all(0.95 < point.rise_tracking_fraction < 1.05 for point in points)
    assert all(0.95 < point.fall_tracking_fraction < 1.05 for point in points)
    target_iq = spec.ibias * spec.output_stage_current_ratio
    assert all(point.source_peak_a > 1.25 * target_iq for point in points)
    assert all(point.sink_peak_a > 1.25 * target_iq for point in points)
    assert points[1].phase_margin_deg >= spec.phase_margin_min_deg
    # The sweep still exposes the load range beyond the nominal specification.
    assert points[-1].phase_margin_deg < points[0].phase_margin_deg
    assert points[-1].phase_margin_deg < spec.phase_margin_min_deg


@ngspice
def test_class_ab_thd_and_crossover_distortion_at_nominal_loads():
    """Static push-pull operation has no material zero-crossing dead zone."""
    spec = SizingSpec(
        vdd=1.0,
        vss=0.0,
        ibias=20e-6,
        cl=2e-12,
        output_stage_current_ratio=1.5,
        gain_min_db=35,
        gbw_min_hz=3e6,
        phase_margin_min_deg=60,
    )
    netlist, result, tech = _size_ptm45(
        "two_stage_opamp_class_ab_single_ended",
        {
            "input_pair": "differential_pair_pmos",
            "load": "active_load_nmos",
            "tail_current": "current_mirror_tail_pmos",
            "compensation": "miller_cap",
            "class_ab_stage": "complementary_class_ab_output",
        },
        spec,
    )
    calibration = calibrate_class_ab_iq(netlist, result, tech, spec)
    assert calibration.converged

    points = characterize_class_ab_linearity(
        netlist, calibration.sizing, tech, spec,
        amplitudes_v=(0.025, 0.05, 0.10, 0.15),
        load_cap_values=(0.5e-12, 2e-12),
        frequency_hz=100e3,
    )

    assert len(points) == 8
    assert all(point.converged for point in points)
    assert max(point.thd_percent for point in points) < 0.6
    assert max(point.crossover_residual_v for point in points) < 1e-3
    for load in (0.5e-12, 2e-12):
        per_load = [point for point in points if point.load_cap_f == load]
        assert [point.thd_percent for point in per_load] == sorted(
            point.thd_percent for point in per_load
        )


@ngspice
def test_class_ab_pvt_sweep_exposes_static_bias_drift():
    """The present tied-gate IQ mechanism is nominally valid but not PVT robust."""
    spec = SizingSpec(
        vdd=1.0,
        vss=0.0,
        ibias=20e-6,
        cl=2e-12,
        output_stage_current_ratio=1.5,
        gain_min_db=35,
        gbw_min_hz=3e6,
        phase_margin_min_deg=60,
    )
    netlist, result, tech = _size_ptm45(
        "two_stage_opamp_class_ab_single_ended",
        {
            "input_pair": "differential_pair_pmos",
            "load": "active_load_nmos",
            "tail_current": "current_mirror_tail_pmos",
            "compensation": "miller_cap",
            "class_ab_stage": "complementary_class_ab_output",
        },
        spec,
    )
    calibration = calibrate_class_ab_iq(netlist, result, tech, spec)
    assert calibration.converged

    points = sweep_class_ab_pvt(
        netlist, calibration.sizing, tech, spec,
        supply_values=(0.9, 1.0, 1.1),
        temperatures_c=(-40.0, 27.0, 125.0),
    )

    assert len(points) == 9
    assert all(point.corner == "nominal" for point in points)
    assert all(point.converged for point in points)
    nominal = next(point for point in points
                   if point.vdd_v == 1.0 and point.temperature_c == 27.0)
    assert nominal.passed
    assert not all(point.passed for point in points)
    assert min(point.phase_margin_deg for point in points) < 60.0
    assert max(point.thd_percent for point in points) > 1.0
    currents = [point.iq_a for point in points]
    assert max(currents) / min(currents) > 5.0
