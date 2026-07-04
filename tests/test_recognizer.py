import pytest

from circuitgenome.synthesizer.loader import load_modules, load_topologies
from circuitgenome.synthesizer.synthesizer import enumerate_circuits
from circuitgenome.synthesizer.netlist import to_flat_spice
from circuitgenome.recognizer.netlist_parser import parse
from circuitgenome.recognizer.subcircuit_recognizer import recognize
from circuitgenome.recognizer.functional_block_recognizer import assign_slots

# For inverter_based_input, enumerate_circuits accepts only the canonical tail
# variant and prunes it to tail_current_absent (0 devices); see
# tail_current_compatibility.py.
_CANONICAL_TAIL = "current_mirror_tail_pmos"

# inverter_based_input is parked as unsupported for synthesis (issue #113:
# no fixed-Vgs sizing path), but the recognizer must still handle external
# netlists containing it, so the round-trip builders opt back in.
_INCLUDE_UNSUPPORTED = {"include_unsupported": True}


def _expected_pattern_name(variant):
    """The pattern each slot's variant should resolve to.  The constructed
    bias generator resolves to constructed_bias when it has PMOS-referenced
    legs or a cascode leg (a floor resistor -- a shape the legacy hook
    cannot claim); purely NMOS-referenced diode/mirror shapes (and the bare
    master) keep the historical diode_connected_mosfet_bias pattern, whose
    hook discovers the identical device set (see
    hooks.constructed_bias_legs)."""
    if variant.category == "bias_generation":
        constructed = any(
            d.terminals.get("g") == "pref" or d.type == "resistor"
            for d in variant.devices
        )
        return "constructed_bias" if constructed else "diode_connected_mosfet_bias"
    return variant.name


_MODULES = None


def _get_modules():
    global _MODULES
    if _MODULES is None:
        _MODULES = load_modules()
    return _MODULES

# 11 combos covering every reachable one_stage_opamp variant: all 5
# input_pair, all 10 load, and all 6 real tail_current variants.  The bias
# generator is constructed per combination from the consumer demands
# (synthesizer/bias_construction.py); _expected_pattern_name resolves which
# recognizer pattern each constructed shape lands on.
_ONE_STAGE_COMBOS = [
    # ── input_pair: differential_pair_pmos ──────────────────────────────────
    ("differential_pair_pmos",            "telescopic_cascode_load_pmos",                 "current_mirror_tail_pmos"),
    ("differential_pair_pmos",            "resistor_load_gnd",                            "resistor_tail_vdd"),
    ("differential_pair_pmos",            "active_load_nmos",                             "cascode_current_mirror_tail_pmos"),
    ("differential_pair_pmos",            "current_source_load_nmos",                     "resistor_tail_vdd"),
    # ── input_pair: differential_pair_nmos ──────────────────────────────────
    ("differential_pair_nmos",            "active_load_pmos",                             "current_mirror_tail_nmos"),
    ("differential_pair_nmos",            "current_source_load_pmos",                     "resistor_tail_gnd"),
    ("differential_pair_nmos",            "resistor_load_vdd",                            "resistor_tail_gnd"),
    ("differential_pair_nmos",            "telescopic_cascode_load_nmos",                 "cascode_current_mirror_tail_nmos"),
    # ── input_pair: degenerated variants ────────────────────────────────────
    ("differential_pair_nmos_degenerated","folded_cascode_load_nmos_input_single_output", "resistor_tail_gnd"),
    ("differential_pair_pmos_degenerated","folded_cascode_load_pmos_input_single_output", "resistor_tail_vdd"),
    # ── input_pair: inverter_based_input (tail pruned to absent) ────────────
    ("inverter_based_input",              "folded_cascode_load_nmos_input_single_output", _CANONICAL_TAIL),
]


@pytest.fixture(scope="module")
def one_stage_fixtures():
    modules = load_modules()
    topology = next(t for t in load_topologies() if t.name == "one_stage_opamp")
    return modules, topology


@pytest.mark.parametrize("input_pair,load,tail_current", _ONE_STAGE_COMBOS)
def test_round_trip_one_stage_opamp(
    one_stage_fixtures, input_pair, load, tail_current
):
    modules, topology = one_stage_fixtures
    simple_modules = {
        "input_pair":      [v for v in modules["input_pair"]      if v.name == input_pair],
        "load":            [v for v in modules["load"]            if v.name == load],
        "tail_current":    [v for v in modules["tail_current"]    if v.name == tail_current],
        "cmfb":            modules["cmfb"],
        "compensation":    modules["compensation"],
        "second_stage":    modules["second_stage"],
    }
    circuit = next(enumerate_circuits(topology, simple_modules, _INCLUDE_UNSUPPORTED))

    sr_result = recognize(parse(to_flat_spice(circuit)))

    assert sr_result.unrecognized_devices == [], (
        f"unrecognized: {[d.ref for d in sr_result.unrecognized_devices]}"
    )

    fbr_result = assign_slots(sr_result, topology)

    for slot_name, variant in circuit.variant_map.items():
        if not variant.devices:
            continue
        expected = _expected_pattern_name(variant)
        assigned = fbr_result.slot_assignments.get(slot_name)
        assert assigned is not None, (
            f"slot {slot_name!r} missing; expected {expected!r}"
        )
        assert assigned.pattern_name == expected, (
            f"slot {slot_name!r}: expected {expected!r}, got {assigned.pattern_name!r}"
        )


# ─── two_stage_opamp_single_ended round-trip ────────────────────────────────
#
# 11 combos cover all 3 compensation variants, all 5 second_stage variants,
# and all 5 input_pair variants across representative base combinations.
# The stage-interface filter (second_stage_compatibility.py) restricts each
# tagged pair to the level-reachable stages: pmos pairs take
# common_source/differential_ota/common_drain, nmos pairs take
# common_source_pmos/common_drain_nmos, inverter_based_input takes any.
# The constructed bias generator picks its rail-5 leg from the second stage
# (common_source/differential_ota/common_drain: gate_vdd;
# common_source_pmos/common_drain_nmos: gate_gnd) and its rail-7 leg from
# the tail's reference diode; see _ONE_STAGE_COMBOS comment.
_TWO_STAGE_COMBOS = [
    # fmt: off
    # input_pair                           load                                        tail_current                         bias_gen                        compensation                        second_stage
    ("differential_pair_pmos",             "telescopic_cascode_load_pmos",             "current_mirror_tail_pmos",          "miller_cap",                       "common_source"),
    ("differential_pair_pmos",             "resistor_load_gnd",                        "resistor_tail_vdd",                 "miller_cap",                       "common_drain"),
    ("differential_pair_pmos",             "active_load_nmos",                         "current_mirror_tail_pmos",          "miller_cap",                       "differential_ota_second_stage"),
    ("differential_pair_pmos",             "current_source_load_nmos",                 "resistor_tail_vdd",                 "miller_cap_with_nulling_resistor", "common_source"),
    ("differential_pair_nmos",             "active_load_pmos",                         "current_mirror_tail_nmos",          "miller_cap_with_nulling_resistor", "common_drain_nmos"),
    ("differential_pair_nmos",             "current_source_load_pmos",                 "resistor_tail_gnd",                 "miller_cap_with_nulling_resistor", "common_source_pmos"),
    ("differential_pair_nmos",             "resistor_load_vdd",                        "resistor_tail_gnd",                 "indirect_compensation",            "common_source_pmos"),
    ("differential_pair_nmos",             "telescopic_cascode_load_nmos",             "resistor_tail_gnd",                 "indirect_compensation",            "common_drain_nmos"),
    ("differential_pair_nmos_degenerated", "folded_cascode_load_nmos_input_single_output", "resistor_tail_gnd",             "indirect_compensation",            "common_drain_nmos"),
    ("differential_pair_pmos_degenerated", "folded_cascode_load_pmos_input_single_output", "resistor_tail_vdd",             "miller_cap",                       "differential_ota_second_stage"),
    ("inverter_based_input",               "folded_cascode_load_pmos_input_single_output", _CANONICAL_TAIL,                 "miller_cap_with_nulling_resistor", "common_drain"),
    # fmt: on
]


@pytest.fixture(scope="module")
def two_stage_fixtures():
    modules = load_modules()
    topology = next(t for t in load_topologies() if t.name == "two_stage_opamp_single_ended")
    return modules, topology


@pytest.mark.parametrize(
    "input_pair,load,tail_current,compensation,second_stage",
    _TWO_STAGE_COMBOS,
)
def test_round_trip_two_stage_opamp(
    two_stage_fixtures,
    input_pair, load, tail_current, compensation, second_stage,
):
    modules, topology = two_stage_fixtures
    simple_modules = {
        "input_pair":      [v for v in modules["input_pair"]      if v.name == input_pair],
        "load":            [v for v in modules["load"]            if v.name == load],
        "tail_current":    [v for v in modules["tail_current"]    if v.name == tail_current],
        "compensation":    [v for v in modules["compensation"]    if v.name == compensation],
        "second_stage":    [v for v in modules["second_stage"]    if v.name == second_stage],
    }
    circuit = next(enumerate_circuits(topology, simple_modules, _INCLUDE_UNSUPPORTED))

    sr_result = recognize(parse(to_flat_spice(circuit)))

    assert sr_result.unrecognized_devices == [], (
        f"unrecognized: {[d.ref for d in sr_result.unrecognized_devices]}"
    )

    fbr_result = assign_slots(sr_result, topology)

    for slot_name, variant in circuit.variant_map.items():
        if not variant.devices:
            continue
        expected = _expected_pattern_name(variant)
        assigned = fbr_result.slot_assignments.get(slot_name)
        assert assigned is not None, (
            f"slot {slot_name!r} missing; expected {expected!r}"
        )
        assert assigned.pattern_name == expected, (
            f"slot {slot_name!r}: expected {expected!r}, got {assigned.pattern_name!r}"
        )


# ─── two_stage_opamp_fully_differential round-trip ──────────────────────────
#
# 11 combos cover both cmfb variants, all 3 compensation variants on each of
# comp_p and comp_n independently (including asymmetric combos 4-7 that
# exercise FBR's same-category disambiguation), all 5 second_stage variants
# (the stage-interface filter restricts pmos pairs to
# common_source/differential_ota/common_drain and nmos pairs to
# common_source_pmos/common_drain_nmos on both output paths), and all 4
# input_pair variants reachable with differential-output
# loads (inverter_based_input excluded: it needs a neutral-load topology).
# Only folded_cascode_load_*_input_differential_output loads produce a real
# (non-absent) cmfb instance; all other loads get cmfb_absent and cannot test
# the cmfb patterns.  A real cmfb makes rail 4 gate_gnd (its NMOS tail
# gate), so every FD-with-real-cmfb constructed generator mixes flavors and
# resolves to the constructed_bias pattern -- the consumer sets the retired
# flavor filter used to route to resistor_bias only (and whose
# current_mirror_tail_nmos pairings hit the historical B1 mis-recognition)
# are exactly the ones the per-leg construction and per-leg recognition now
# handle first-class.
_TWO_STAGE_FULLY_DIFF_COMBOS = [
    # fmt: off
    # input_pair                             load                                                  tail_current                         bias_gen                        cmfb                     comp_p                              comp_n                              second_stage_p                  second_stage_n
    ("differential_pair_pmos",              "folded_cascode_load_pmos_input_differential_output",  "current_mirror_tail_pmos",          "resistive_sense_cmfb",  "miller_cap",                       "miller_cap",                       "common_source",                "common_source"),
    ("differential_pair_pmos",              "folded_cascode_load_pmos_input_differential_output",  "resistor_tail_vdd",                 "dda_cmfb",              "miller_cap_with_nulling_resistor",  "miller_cap_with_nulling_resistor",  "differential_ota_second_stage", "differential_ota_second_stage"),
    ("differential_pair_pmos",              "folded_cascode_load_pmos_input_differential_output",  "cascode_current_mirror_tail_pmos",  "resistive_sense_cmfb",  "indirect_compensation",            "indirect_compensation",            "differential_ota_second_stage","differential_ota_second_stage"),
    ("differential_pair_pmos",              "folded_cascode_load_pmos_input_differential_output",  "current_mirror_tail_pmos",          "dda_cmfb",              "miller_cap",                       "miller_cap_with_nulling_resistor",  "common_source",                "common_drain"),
    ("differential_pair_nmos",              "folded_cascode_load_nmos_input_differential_output",  "resistor_tail_gnd",                 "resistive_sense_cmfb",  "miller_cap",                       "indirect_compensation",            "common_source_pmos",           "common_source_pmos"),
    ("differential_pair_nmos",              "folded_cascode_load_nmos_input_differential_output",  "resistor_tail_gnd",                 "dda_cmfb",              "indirect_compensation",            "miller_cap",                       "common_drain_nmos",            "common_drain_nmos"),
    ("differential_pair_nmos",              "folded_cascode_load_nmos_input_differential_output",  "cascode_current_mirror_tail_nmos",  "resistive_sense_cmfb",  "miller_cap_with_nulling_resistor",  "indirect_compensation",            "common_source_pmos",           "common_drain_nmos"),
    ("differential_pair_nmos",              "folded_cascode_load_nmos_input_differential_output",  "resistor_tail_gnd",                 "dda_cmfb",              "miller_cap",                       "miller_cap_with_nulling_resistor",  "common_source_pmos",           "common_source_pmos"),
    ("differential_pair_pmos_degenerated",  "folded_cascode_load_pmos_input_differential_output",  "resistor_tail_vdd",                 "resistive_sense_cmfb",  "miller_cap_with_nulling_resistor",  "miller_cap_with_nulling_resistor",  "common_source",                "common_source"),
    ("differential_pair_nmos_degenerated",  "folded_cascode_load_nmos_input_differential_output",  "cascode_current_mirror_tail_nmos",  "dda_cmfb",              "indirect_compensation",            "indirect_compensation",            "common_drain_nmos",            "common_drain_nmos"),
    ("differential_pair_pmos",              "folded_cascode_load_pmos_input_differential_output",  "resistor_tail_vdd",                 "dda_cmfb",              "indirect_compensation",            "indirect_compensation",            "common_source",                "differential_ota_second_stage"),
    # fmt: on
]


@pytest.fixture(scope="module")
def two_stage_fully_diff_fixtures():
    modules = load_modules()
    topology = next(t for t in load_topologies() if t.name == "two_stage_opamp_fully_differential")
    return modules, topology


@pytest.mark.parametrize(
    "input_pair,load,tail_current,cmfb,comp_p,comp_n,second_stage_p,second_stage_n",
    _TWO_STAGE_FULLY_DIFF_COMBOS,
)
def test_round_trip_two_stage_fully_diff(
    two_stage_fully_diff_fixtures,
    input_pair, load, tail_current, cmfb,
    comp_p, comp_n, second_stage_p, second_stage_n,
):
    modules, topology = two_stage_fully_diff_fixtures
    simple_modules = {
        "input_pair":      [v for v in modules["input_pair"]      if v.name == input_pair],
        "load":            [v for v in modules["load"]            if v.name == load],
        "tail_current":    [v for v in modules["tail_current"]    if v.name == tail_current],
        "cmfb":            [v for v in modules["cmfb"]            if v.name == cmfb],
        # Both p-side and n-side variants must be present so enumerate_circuits
        # can fill comp_p and comp_n (or second_stage_p and second_stage_n)
        # independently; it maps by slot.category, not slot.name.
        "compensation":    [v for v in modules["compensation"]    if v.name in (comp_p, comp_n)],
        "second_stage":    [v for v in modules["second_stage"]    if v.name in (second_stage_p, second_stage_n)],
    }
    circuit = next(enumerate_circuits(topology, simple_modules, _INCLUDE_UNSUPPORTED))

    sr_result = recognize(parse(to_flat_spice(circuit)))

    assert sr_result.unrecognized_devices == [], (
        f"unrecognized: {[d.ref for d in sr_result.unrecognized_devices]}"
    )

    fbr_result = assign_slots(sr_result, topology)

    for slot_name, variant in circuit.variant_map.items():
        if not variant.devices:
            continue
        expected = _expected_pattern_name(variant)
        assigned = fbr_result.slot_assignments.get(slot_name)
        assert assigned is not None, (
            f"slot {slot_name!r} missing; expected {expected!r}"
        )
        assert assigned.pattern_name == expected, (
            f"slot {slot_name!r}: expected {expected!r}, got {assigned.pattern_name!r}"
        )


# ── Three-stage topologies ───────────────────────────────────────────────────
#
# All four 3-stage topologies reuse the existing 36 SR patterns:
#   - third_stage slot has category 'second_stage' → matched by second_stage patterns
#   - comp1/comp2 (SE) and comp1_p/comp2_p/comp1_n/comp2_n (FD) use compensation patterns
#
# The simple_modules pool for 'second_stage' must include variants for BOTH
# second_stage and third_stage slots; similarly 'compensation' must include
# variants for all comp slots, since enumerate_circuits maps by slot.category.
#
# The FBR assigned_ids mechanism (from #31) correctly handles 4 same-category
# slots — connectivity scoring on distinct nets (net_mid1/net_mid2, or
# net_loadout1/net_loadout2/net_mid2_p/net_mid2_n) prevents any ties.

_THREE_STAGE_SE_COMBOS = [
    # (input_pair, load, tail_current, bias_gen, second_stage, third_stage, comp1, comp2)
    # Polarity rule: pmos input pair → pmos_input-polarity loads (active_load_nmos,
    # current_source_load_nmos, folded_cascode_load_pmos_*, telescopic_cascode_load_pmos).
    # nmos input pair → nmos_input-polarity loads (active_load_pmos,
    # current_source_load_pmos, folded_cascode_load_nmos_*, telescopic_cascode_load_nmos).
    # Covers: all 3 comp variants, all 5 ss/ts variants, both polarities,
    # degenerated pairs, several load types. The stage-interface filter
    # constrains only the ss slot (pmos pair ->
    # common_source/ota/common_drain, nmos pair ->
    # common_source_pmos/common_drain_nmos); the ts slot senses the
    # wide-swing gm2 output and takes any variant.
    # The constructed generator serves rails 5 AND 6 with per-rail legs
    # (mixed stage flavors included -- no combo needs avoiding).
    ("differential_pair_pmos", "active_load_nmos", "current_mirror_tail_pmos",
     "common_source", "common_source",
     "miller_cap", "miller_cap"),
    ("differential_pair_pmos", "active_load_nmos", "resistor_tail_vdd",
     "differential_ota_second_stage", "common_source_pmos",
     "miller_cap_with_nulling_resistor", "miller_cap_with_nulling_resistor"),
    ("differential_pair_nmos", "active_load_pmos", "resistor_tail_gnd",
     "common_source_pmos", "common_source_pmos",
     "indirect_compensation", "indirect_compensation"),
    ("differential_pair_pmos", "current_source_load_nmos", "resistor_tail_vdd",
     "common_source", "common_drain",
     "miller_cap", "indirect_compensation"),
    ("differential_pair_nmos", "current_source_load_pmos", "cascode_current_mirror_tail_nmos",
     "common_drain_nmos", "common_source",
     "indirect_compensation", "miller_cap"),
    ("differential_pair_pmos", "folded_cascode_load_pmos_input_single_output",
     "cascode_current_mirror_tail_pmos", 
     "differential_ota_second_stage", "common_source",
     "miller_cap_with_nulling_resistor", "indirect_compensation"),
    ("differential_pair_pmos_degenerated", "active_load_nmos",
     "cascode_current_mirror_tail_pmos", 
     "common_source", "differential_ota_second_stage",
     "miller_cap", "miller_cap_with_nulling_resistor"),
    ("differential_pair_nmos_degenerated", "active_load_pmos",
     "current_mirror_tail_nmos",
     "common_drain_nmos", "common_drain_nmos",
     "miller_cap_with_nulling_resistor", "miller_cap"),
    ("differential_pair_pmos", "telescopic_cascode_load_pmos",
     "resistor_tail_vdd", 
     "differential_ota_second_stage", "common_drain",
     "indirect_compensation", "miller_cap_with_nulling_resistor"),
]

_THREE_STAGE_FD_COMBOS = [
    # (input_pair, load, tail_current, bias_gen, cmfb,
    #  ss_p, ts_p, c1_p, c2_p, ss_n, ts_n, c1_n, c2_n)
    # Covers: both cmfb variants, all 3 comp variants, all 5 ss/ts variants
    # (ss slots obey the stage-interface filter: pmos pair ->
    # common_source/ota/common_drain, nmos pair ->
    # common_source_pmos/common_drain_nmos; ts slots are unconstrained),
    # both polarities, degenerated pairs, cross-path asymmetry.
    # FBR assigns 4 same-category comp slots and 4 same-category ss slots via
    # connectivity scoring on distinct nets.
    # The real cmfb makes rail 4 gate_gnd, so every combo's constructed
    # generator has PMOS-referenced legs and resolves to constructed_bias.
    ("differential_pair_pmos", "folded_cascode_load_pmos_input_differential_output",
     "current_mirror_tail_pmos", "resistive_sense_cmfb",
     "common_source", "common_source", "miller_cap", "miller_cap",
     "common_source", "common_source", "miller_cap", "miller_cap"),
    ("differential_pair_pmos", "folded_cascode_load_pmos_input_differential_output",
     "resistor_tail_vdd", "dda_cmfb",
     "differential_ota_second_stage", "differential_ota_second_stage",
     "miller_cap_with_nulling_resistor", "miller_cap_with_nulling_resistor",
     "differential_ota_second_stage", "differential_ota_second_stage",
     "miller_cap_with_nulling_resistor", "miller_cap_with_nulling_resistor"),
    ("differential_pair_nmos", "folded_cascode_load_nmos_input_differential_output",
     "cascode_current_mirror_tail_nmos", "resistive_sense_cmfb",
     "common_source_pmos", "common_source_pmos",
     "indirect_compensation", "indirect_compensation",
     "common_source_pmos", "common_source_pmos",
     "indirect_compensation", "indirect_compensation"),
    ("differential_pair_pmos", "folded_cascode_load_pmos_input_differential_output",
     "resistor_tail_vdd", "dda_cmfb",
     "common_source", "common_source", "miller_cap", "miller_cap",
     "common_source", "common_source", "indirect_compensation", "indirect_compensation"),
    ("differential_pair_nmos", "folded_cascode_load_nmos_input_differential_output",
     "cascode_current_mirror_tail_nmos", "dda_cmfb",
     "common_drain_nmos", "common_source", "miller_cap_with_nulling_resistor", "miller_cap_with_nulling_resistor",
     "common_drain_nmos", "common_source", "miller_cap_with_nulling_resistor", "miller_cap_with_nulling_resistor"),
    ("differential_pair_pmos", "folded_cascode_load_pmos_input_differential_output",
     "cascode_current_mirror_tail_pmos", "resistive_sense_cmfb",
     "common_source", "common_drain", "miller_cap", "miller_cap",
     "common_source", "common_drain", "miller_cap", "miller_cap"),
    ("differential_pair_nmos", "folded_cascode_load_nmos_input_differential_output",
     "resistor_tail_gnd", "resistive_sense_cmfb",
     "common_source_pmos", "common_source", "indirect_compensation", "indirect_compensation",
     "common_source_pmos", "common_source", "indirect_compensation", "indirect_compensation"),
    ("differential_pair_pmos_degenerated", "folded_cascode_load_pmos_input_differential_output",
     "resistor_tail_vdd", "resistive_sense_cmfb",
     "common_source", "common_source",
     "miller_cap_with_nulling_resistor", "miller_cap_with_nulling_resistor",
     "common_source", "common_source",
     "miller_cap_with_nulling_resistor", "miller_cap_with_nulling_resistor"),
    ("differential_pair_nmos_degenerated", "folded_cascode_load_nmos_input_differential_output",
     "cascode_current_mirror_tail_nmos", "dda_cmfb",
     "common_drain_nmos", "common_drain_nmos", "indirect_compensation", "indirect_compensation",
     "common_drain_nmos", "common_drain_nmos", "indirect_compensation", "indirect_compensation"),
    ("differential_pair_pmos", "folded_cascode_load_pmos_input_differential_output",
     "current_mirror_tail_pmos", "dda_cmfb",
     "common_source", "common_source",
     "miller_cap_with_nulling_resistor", "indirect_compensation",
     "common_source", "common_source",
     "miller_cap_with_nulling_resistor", "indirect_compensation"),
    ("differential_pair_nmos", "folded_cascode_load_nmos_input_differential_output",
     "resistor_tail_gnd", "resistive_sense_cmfb",
     "common_drain_nmos", "common_drain_nmos", "miller_cap", "miller_cap",
     "common_drain_nmos", "common_drain_nmos", "miller_cap", "miller_cap"),
]


@pytest.fixture(scope="module")
def three_stage_nmc_se_fixtures():
    modules = load_modules()
    topology = next(t for t in load_topologies() if t.name == "three_stage_opamp_nmc_single_ended")
    return modules, topology


@pytest.fixture(scope="module")
def three_stage_rnmc_se_fixtures():
    modules = load_modules()
    topology = next(t for t in load_topologies() if t.name == "three_stage_opamp_rnmc_single_ended")
    return modules, topology


@pytest.fixture(scope="module")
def three_stage_nmc_fd_fixtures():
    modules = load_modules()
    topology = next(t for t in load_topologies() if t.name == "three_stage_opamp_nmc_fully_differential")
    return modules, topology


@pytest.fixture(scope="module")
def three_stage_rnmc_fd_fixtures():
    modules = load_modules()
    topology = next(t for t in load_topologies() if t.name == "three_stage_opamp_rnmc_fully_differential")
    return modules, topology


def _run_three_stage_se(modules, topology, input_pair, load, tail_current,
                        second_stage, third_stage, comp1, comp2):
    simple_modules = {
        "input_pair":      [v for v in modules["input_pair"]      if v.name == input_pair],
        "load":            [v for v in modules["load"]            if v.name == load],
        "tail_current":    [v for v in modules["tail_current"]    if v.name == tail_current],
        # third_stage slot has category 'second_stage'; both slots draw from this pool.
        "second_stage":    [v for v in modules["second_stage"]    if v.name in (second_stage, third_stage)],
        # comp1 and comp2 both have category 'compensation'; both slots draw from this pool.
        "compensation":    [v for v in modules["compensation"]    if v.name in (comp1, comp2)],
    }
    circuit = next(enumerate_circuits(topology, simple_modules, _INCLUDE_UNSUPPORTED))
    sr_result = recognize(parse(to_flat_spice(circuit)))
    assert sr_result.unrecognized_devices == [], (
        f"unrecognized: {[d.ref for d in sr_result.unrecognized_devices]}"
    )
    fbr_result = assign_slots(sr_result, topology)
    for slot_name, variant in circuit.variant_map.items():
        if not variant.devices:
            continue
        expected = _expected_pattern_name(variant)
        assigned = fbr_result.slot_assignments.get(slot_name)
        assert assigned is not None, (
            f"slot {slot_name!r} missing; expected {expected!r}"
        )
        assert assigned.pattern_name == expected, (
            f"slot {slot_name!r}: expected {expected!r}, got {assigned.pattern_name!r}"
        )


def _run_three_stage_fd(modules, topology, input_pair, load, tail_current, cmfb,
                        ss_p, ts_p, c1_p, c2_p, ss_n, ts_n, c1_n, c2_n):
    simple_modules = {
        "input_pair":      [v for v in modules["input_pair"]      if v.name == input_pair],
        "load":            [v for v in modules["load"]            if v.name == load],
        "tail_current":    [v for v in modules["tail_current"]    if v.name == tail_current],
        "cmfb":            [v for v in modules["cmfb"]            if v.name == cmfb],
        # 4 second_stage-category slots (ss_p, ts_p, ss_n, ts_n) all draw from this pool.
        "second_stage":    [v for v in modules["second_stage"]    if v.name in (ss_p, ts_p, ss_n, ts_n)],
        # 4 compensation-category slots (c1_p, c2_p, c1_n, c2_n) all draw from this pool.
        "compensation":    [v for v in modules["compensation"]    if v.name in (c1_p, c2_p, c1_n, c2_n)],
    }
    circuit = next(enumerate_circuits(topology, simple_modules, _INCLUDE_UNSUPPORTED))
    sr_result = recognize(parse(to_flat_spice(circuit)))
    assert sr_result.unrecognized_devices == [], (
        f"unrecognized: {[d.ref for d in sr_result.unrecognized_devices]}"
    )
    fbr_result = assign_slots(sr_result, topology)
    for slot_name, variant in circuit.variant_map.items():
        if not variant.devices:
            continue
        expected = _expected_pattern_name(variant)
        assigned = fbr_result.slot_assignments.get(slot_name)
        assert assigned is not None, (
            f"slot {slot_name!r} missing; expected {expected!r}"
        )
        assert assigned.pattern_name == expected, (
            f"slot {slot_name!r}: expected {expected!r}, got {assigned.pattern_name!r}"
        )


@pytest.mark.parametrize(
    "input_pair,load,tail_current,second_stage,third_stage,comp1,comp2",
    _THREE_STAGE_SE_COMBOS,
)
def test_round_trip_three_stage_nmc_se(
    three_stage_nmc_se_fixtures,
    input_pair, load, tail_current, second_stage, third_stage, comp1, comp2,
):
    modules, topology = three_stage_nmc_se_fixtures
    _run_three_stage_se(modules, topology, input_pair, load, tail_current,
                        second_stage, third_stage, comp1, comp2)


@pytest.mark.parametrize(
    "input_pair,load,tail_current,second_stage,third_stage,comp1,comp2",
    _THREE_STAGE_SE_COMBOS,
)
def test_round_trip_three_stage_rnmc_se(
    three_stage_rnmc_se_fixtures,
    input_pair, load, tail_current, second_stage, third_stage, comp1, comp2,
):
    modules, topology = three_stage_rnmc_se_fixtures
    _run_three_stage_se(modules, topology, input_pair, load, tail_current,
                        second_stage, third_stage, comp1, comp2)


@pytest.mark.parametrize(
    "input_pair,load,tail_current,cmfb,ss_p,ts_p,c1_p,c2_p,ss_n,ts_n,c1_n,c2_n",
    _THREE_STAGE_FD_COMBOS,
)
def test_round_trip_three_stage_nmc_fd(
    three_stage_nmc_fd_fixtures,
    input_pair, load, tail_current, cmfb,
    ss_p, ts_p, c1_p, c2_p, ss_n, ts_n, c1_n, c2_n,
):
    modules, topology = three_stage_nmc_fd_fixtures
    _run_three_stage_fd(modules, topology, input_pair, load, tail_current, cmfb,
                        ss_p, ts_p, c1_p, c2_p, ss_n, ts_n, c1_n, c2_n)


@pytest.mark.parametrize(
    "input_pair,load,tail_current,cmfb,ss_p,ts_p,c1_p,c2_p,ss_n,ts_n,c1_n,c2_n",
    _THREE_STAGE_FD_COMBOS,
)
def test_round_trip_three_stage_rnmc_fd(
    three_stage_rnmc_fd_fixtures,
    input_pair, load, tail_current, cmfb,
    ss_p, ts_p, c1_p, c2_p, ss_n, ts_n, c1_n, c2_n,
):
    modules, topology = three_stage_rnmc_fd_fixtures
    _run_three_stage_fd(modules, topology, input_pair, load, tail_current, cmfb,
                        ss_p, ts_p, c1_p, c2_p, ss_n, ts_n, c1_n, c2_n)
