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

_MODULES = None


def _get_modules():
    global _MODULES
    if _MODULES is None:
        _MODULES = load_modules()
    return _MODULES

# 11 combos covering every reachable one_stage_opamp variant: all 5
# input_pair, all 10 load, all 6 real tail_current, and all 3 bias_generation
# variants.  Combo selection avoids two known structural ambiguities:
#   - resistor_bias is paired with resistor_tail_* + a bias-rail-needing load
#     (current_source/folded_cascode/telescopic) to avoid the B1 spurious
#     match where current_mirror_tail's diode-connected m1 mimics a
#     magic_battery_bias nmos_leg, causing FBR to prefer magic_battery_bias.
#   - magic_battery_bias is paired with current_mirror_tail_* or
#     active_load_nmos+current_mirror_tail_pmos so rail 7 is present;
#     no 0-rail combos (where mref-only magic_battery_bias and resistor_bias
#     are structurally identical) are included.
_ONE_STAGE_COMBOS = [
    # ── input_pair: differential_pair_pmos ──────────────────────────────────
    ("differential_pair_pmos",            "telescopic_cascode_load_pmos",                 "current_mirror_tail_pmos",         "diode_connected_mosfet_bias"),
    ("differential_pair_pmos",            "resistor_load_gnd",                            "resistor_tail_vdd",                "diode_connected_mosfet_bias"),
    ("differential_pair_pmos",            "active_load_nmos",                             "current_mirror_tail_pmos",         "magic_battery_bias"),
    ("differential_pair_pmos",            "current_source_load_nmos",                     "cascode_current_mirror_tail_pmos", "diode_connected_mosfet_bias"),
    # ── input_pair: differential_pair_nmos ──────────────────────────────────
    ("differential_pair_nmos",            "active_load_pmos",                             "current_mirror_tail_nmos",         "magic_battery_bias"),
    ("differential_pair_nmos",            "current_source_load_pmos",                     "resistor_tail_gnd",                "resistor_bias"),
    ("differential_pair_nmos",            "resistor_load_vdd",                            "resistor_tail_gnd",                "diode_connected_mosfet_bias"),
    ("differential_pair_nmos",            "telescopic_cascode_load_nmos",                 "resistor_tail_gnd",                "resistor_bias"),
    # ── input_pair: degenerated variants ────────────────────────────────────
    ("differential_pair_nmos_degenerated","folded_cascode_load_nmos_input_single_output", "cascode_current_mirror_tail_nmos", "diode_connected_mosfet_bias"),
    ("differential_pair_pmos_degenerated","folded_cascode_load_pmos_input_single_output", "cascode_current_mirror_tail_pmos", "magic_battery_bias"),
    # ── input_pair: inverter_based_input (tail pruned to absent) ────────────
    ("inverter_based_input",              "folded_cascode_load_pmos_input_single_output", _CANONICAL_TAIL,                    "diode_connected_mosfet_bias"),
]


@pytest.fixture(scope="module")
def one_stage_fixtures():
    modules = load_modules()
    topology = next(t for t in load_topologies() if t.name == "one_stage_opamp")
    return modules, topology


@pytest.mark.parametrize("input_pair,load,tail_current,bias_generation", _ONE_STAGE_COMBOS)
def test_round_trip_one_stage_opamp(
    one_stage_fixtures, input_pair, load, tail_current, bias_generation
):
    modules, topology = one_stage_fixtures
    simple_modules = {
        "input_pair":      [v for v in modules["input_pair"]      if v.name == input_pair],
        "load":            [v for v in modules["load"]            if v.name == load],
        "tail_current":    [v for v in modules["tail_current"]    if v.name == tail_current],
        "bias_generation": [v for v in modules["bias_generation"] if v.name == bias_generation],
        "cmfb":            modules["cmfb"],
        "compensation":    modules["compensation"],
        "second_stage":    modules["second_stage"],
    }
    circuit = next(enumerate_circuits(topology, simple_modules))

    sr_result = recognize(parse(to_flat_spice(circuit)))

    assert sr_result.unrecognized_devices == [], (
        f"unrecognized: {[d.ref for d in sr_result.unrecognized_devices]}"
    )

    fbr_result = assign_slots(sr_result, topology)

    for slot_name, variant in circuit.variant_map.items():
        if not variant.devices:
            continue
        assigned = fbr_result.slot_assignments.get(slot_name)
        assert assigned is not None, (
            f"slot {slot_name!r} missing; expected {variant.name!r}"
        )
        assert assigned.pattern_name == variant.name, (
            f"slot {slot_name!r}: expected {variant.name!r}, got {assigned.pattern_name!r}"
        )  # end test_round_trip_one_stage_opamp


# ─── two_stage_opamp_single_ended round-trip ────────────────────────────────
#
# 11 combos cover all 3 compensation variants, all 3 second_stage variants,
# and all 5 input_pair variants across representative base combinations.
# Combos avoid the known B1 ambiguity (current_mirror_tail_nmos + resistor_bias
# → spurious magic_battery_bias wins); see _ONE_STAGE_COMBOS comment above.
_TWO_STAGE_COMBOS = [
    # fmt: off
    # input_pair                           load                                        tail_current                         bias_gen                        compensation                        second_stage
    ("differential_pair_pmos",             "telescopic_cascode_load_pmos",             "current_mirror_tail_pmos",          "diode_connected_mosfet_bias",  "miller_cap",                       "common_source"),
    ("differential_pair_pmos",             "resistor_load_gnd",                        "resistor_tail_vdd",                 "diode_connected_mosfet_bias",  "miller_cap",                       "common_drain"),
    ("differential_pair_pmos",             "active_load_nmos",                         "current_mirror_tail_pmos",          "magic_battery_bias",           "miller_cap",                       "differential_ota_second_stage"),
    ("differential_pair_pmos",             "current_source_load_nmos",                 "cascode_current_mirror_tail_pmos",  "diode_connected_mosfet_bias",  "miller_cap_with_nulling_resistor", "common_source"),
    ("differential_pair_nmos",             "active_load_pmos",                         "current_mirror_tail_nmos",          "magic_battery_bias",           "miller_cap_with_nulling_resistor", "common_drain"),
    ("differential_pair_nmos",             "current_source_load_pmos",                 "resistor_tail_gnd",                 "resistor_bias",                "miller_cap_with_nulling_resistor", "differential_ota_second_stage"),
    ("differential_pair_nmos",             "resistor_load_vdd",                        "resistor_tail_gnd",                 "diode_connected_mosfet_bias",  "indirect_compensation",            "common_source"),
    ("differential_pair_nmos",             "telescopic_cascode_load_nmos",             "resistor_tail_gnd",                 "resistor_bias",                "indirect_compensation",            "common_drain"),
    ("differential_pair_nmos_degenerated", "folded_cascode_load_nmos_input_single_output", "cascode_current_mirror_tail_nmos", "diode_connected_mosfet_bias", "indirect_compensation",          "differential_ota_second_stage"),
    ("differential_pair_pmos_degenerated", "folded_cascode_load_pmos_input_single_output", "cascode_current_mirror_tail_pmos", "magic_battery_bias",          "miller_cap",                     "common_source"),
    ("inverter_based_input",               "folded_cascode_load_pmos_input_single_output", _CANONICAL_TAIL,                 "diode_connected_mosfet_bias",  "miller_cap_with_nulling_resistor", "common_drain"),
    # fmt: on
]


@pytest.fixture(scope="module")
def two_stage_fixtures():
    modules = load_modules()
    topology = next(t for t in load_topologies() if t.name == "two_stage_opamp_single_ended")
    return modules, topology


@pytest.mark.parametrize(
    "input_pair,load,tail_current,bias_generation,compensation,second_stage",
    _TWO_STAGE_COMBOS,
)
def test_round_trip_two_stage_opamp(
    two_stage_fixtures,
    input_pair, load, tail_current, bias_generation, compensation, second_stage,
):
    modules, topology = two_stage_fixtures
    simple_modules = {
        "input_pair":      [v for v in modules["input_pair"]      if v.name == input_pair],
        "load":            [v for v in modules["load"]            if v.name == load],
        "tail_current":    [v for v in modules["tail_current"]    if v.name == tail_current],
        "bias_generation": [v for v in modules["bias_generation"]  if v.name == bias_generation],
        "compensation":    [v for v in modules["compensation"]    if v.name == compensation],
        "second_stage":    [v for v in modules["second_stage"]    if v.name == second_stage],
    }
    circuit = next(enumerate_circuits(topology, simple_modules))

    sr_result = recognize(parse(to_flat_spice(circuit)))

    assert sr_result.unrecognized_devices == [], (
        f"unrecognized: {[d.ref for d in sr_result.unrecognized_devices]}"
    )

    fbr_result = assign_slots(sr_result, topology)

    for slot_name, variant in circuit.variant_map.items():
        if not variant.devices:
            continue
        assigned = fbr_result.slot_assignments.get(slot_name)
        assert assigned is not None, (
            f"slot {slot_name!r} missing; expected {variant.name!r}"
        )
        assert assigned.pattern_name == variant.name, (
            f"slot {slot_name!r}: expected {variant.name!r}, got {assigned.pattern_name!r}"
        )  # end test_round_trip_two_stage_opamp


# ─── two_stage_opamp_fully_differential round-trip ──────────────────────────
#
# 11 combos cover both cmfb variants, all 3 compensation variants on each of
# comp_p and comp_n independently (including asymmetric combos 4-7 that
# exercise FBR's same-category disambiguation), all 3 second_stage variants on
# each side, and all 4 input_pair variants reachable with differential-output
# loads (inverter_based_input excluded: it needs a neutral-load topology).
# Only folded_cascode_load_*_input_differential_output loads produce a real
# (non-absent) cmfb instance; all other loads get cmfb_absent and cannot test
# the cmfb patterns. Combos avoid the B1 ambiguity (current_mirror_tail_nmos
# + resistor_bias → spurious magic_battery_bias).
_TWO_STAGE_FULLY_DIFF_COMBOS = [
    # fmt: off
    # input_pair                             load                                                  tail_current                         bias_gen                        cmfb                     comp_p                              comp_n                              second_stage_p                  second_stage_n
    ("differential_pair_pmos",              "folded_cascode_load_pmos_input_differential_output",  "current_mirror_tail_pmos",          "diode_connected_mosfet_bias",  "resistive_sense_cmfb",  "miller_cap",                       "miller_cap",                       "common_source",                "common_source"),
    ("differential_pair_pmos",              "folded_cascode_load_pmos_input_differential_output",  "cascode_current_mirror_tail_pmos",  "magic_battery_bias",           "dda_cmfb",              "miller_cap_with_nulling_resistor",  "miller_cap_with_nulling_resistor",  "common_drain",                 "common_drain"),
    ("differential_pair_pmos",              "folded_cascode_load_pmos_input_differential_output",  "resistor_tail_vdd",                 "diode_connected_mosfet_bias",  "resistive_sense_cmfb",  "indirect_compensation",            "indirect_compensation",            "differential_ota_second_stage","differential_ota_second_stage"),
    ("differential_pair_pmos",              "folded_cascode_load_pmos_input_differential_output",  "current_mirror_tail_pmos",          "magic_battery_bias",           "dda_cmfb",              "miller_cap",                       "miller_cap_with_nulling_resistor",  "common_source",                "common_drain"),
    ("differential_pair_nmos",              "folded_cascode_load_nmos_input_differential_output",  "current_mirror_tail_nmos",          "magic_battery_bias",           "resistive_sense_cmfb",  "miller_cap",                       "indirect_compensation",            "common_source",                "differential_ota_second_stage"),
    ("differential_pair_nmos",              "folded_cascode_load_nmos_input_differential_output",  "resistor_tail_gnd",                 "diode_connected_mosfet_bias",  "dda_cmfb",              "indirect_compensation",            "miller_cap",                       "common_drain",                 "common_source"),
    ("differential_pair_nmos",              "folded_cascode_load_nmos_input_differential_output",  "cascode_current_mirror_tail_nmos",  "diode_connected_mosfet_bias",  "resistive_sense_cmfb",  "miller_cap_with_nulling_resistor",  "indirect_compensation",            "differential_ota_second_stage","common_drain"),
    ("differential_pair_nmos",              "folded_cascode_load_nmos_input_differential_output",  "resistor_tail_gnd",                 "resistor_bias",                "dda_cmfb",              "miller_cap",                       "miller_cap_with_nulling_resistor",  "common_source",                "common_source"),
    ("differential_pair_pmos_degenerated",  "folded_cascode_load_pmos_input_differential_output",  "cascode_current_mirror_tail_pmos",  "magic_battery_bias",           "resistive_sense_cmfb",  "miller_cap_with_nulling_resistor",  "miller_cap_with_nulling_resistor",  "common_source",                "common_drain"),
    ("differential_pair_nmos_degenerated",  "folded_cascode_load_nmos_input_differential_output",  "cascode_current_mirror_tail_nmos",  "diode_connected_mosfet_bias",  "dda_cmfb",              "indirect_compensation",            "indirect_compensation",            "differential_ota_second_stage","differential_ota_second_stage"),
    ("differential_pair_pmos",              "folded_cascode_load_pmos_input_differential_output",  "resistor_tail_vdd",                 "resistor_bias",                "dda_cmfb",              "indirect_compensation",            "indirect_compensation",            "common_source",                "differential_ota_second_stage"),
    # fmt: on
]


@pytest.fixture(scope="module")
def two_stage_fully_diff_fixtures():
    modules = load_modules()
    topology = next(t for t in load_topologies() if t.name == "two_stage_opamp_fully_differential")
    return modules, topology


@pytest.mark.parametrize(
    "input_pair,load,tail_current,bias_generation,cmfb,comp_p,comp_n,second_stage_p,second_stage_n",
    _TWO_STAGE_FULLY_DIFF_COMBOS,
)
def test_round_trip_two_stage_fully_diff(
    two_stage_fully_diff_fixtures,
    input_pair, load, tail_current, bias_generation, cmfb,
    comp_p, comp_n, second_stage_p, second_stage_n,
):
    modules, topology = two_stage_fully_diff_fixtures
    simple_modules = {
        "input_pair":      [v for v in modules["input_pair"]      if v.name == input_pair],
        "load":            [v for v in modules["load"]            if v.name == load],
        "tail_current":    [v for v in modules["tail_current"]    if v.name == tail_current],
        "bias_generation": [v for v in modules["bias_generation"] if v.name == bias_generation],
        "cmfb":            [v for v in modules["cmfb"]            if v.name == cmfb],
        # Both p-side and n-side variants must be present so enumerate_circuits
        # can fill comp_p and comp_n (or second_stage_p and second_stage_n)
        # independently; it maps by slot.category, not slot.name.
        "compensation":    [v for v in modules["compensation"]    if v.name in (comp_p, comp_n)],
        "second_stage":    [v for v in modules["second_stage"]    if v.name in (second_stage_p, second_stage_n)],
    }
    circuit = next(enumerate_circuits(topology, simple_modules))

    sr_result = recognize(parse(to_flat_spice(circuit)))

    assert sr_result.unrecognized_devices == [], (
        f"unrecognized: {[d.ref for d in sr_result.unrecognized_devices]}"
    )

    fbr_result = assign_slots(sr_result, topology)

    for slot_name, variant in circuit.variant_map.items():
        if not variant.devices:
            continue
        assigned = fbr_result.slot_assignments.get(slot_name)
        assert assigned is not None, (
            f"slot {slot_name!r} missing; expected {variant.name!r}"
        )
        assert assigned.pattern_name == variant.name, (
            f"slot {slot_name!r}: expected {variant.name!r}, got {assigned.pattern_name!r}"
        )
