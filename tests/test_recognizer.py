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

# 9 combos covering every reachable one_stage_opamp variant: all 5
# input_pair, all 8 single-ended-reachable load variants
# (current_source_load_* are pruned from single-ended topologies by
# load_branch_compatibility.py, issue #112), and all 6 real tail_current
# variants.  The bias generator is constructed per combination from the
# consumer demands (synthesizer/bias_construction.py);
# _expected_pattern_name resolves which recognizer pattern each constructed
# shape lands on.
_ONE_STAGE_COMBOS = [
    # ── input_pair: differential_pair_pmos ──────────────────────────────────
    ("differential_pair_pmos",            "telescopic_cascode_load_pmos",                 "current_mirror_tail_pmos"),
    ("differential_pair_pmos",            "telescopic_cascode_load_wideswing_pmos",       "current_mirror_tail_pmos"),
    ("differential_pair_pmos",            "resistor_load_gnd",                            "resistor_tail_vdd"),
    ("differential_pair_pmos",            "active_load_nmos",                             "cascode_current_mirror_tail_pmos"),
    # ── input_pair: differential_pair_nmos ──────────────────────────────────
    ("differential_pair_nmos",            "active_load_pmos",                             "current_mirror_tail_nmos"),
    ("differential_pair_nmos",            "resistor_load_vdd",                            "resistor_tail_gnd"),
    ("differential_pair_nmos",            "telescopic_cascode_load_nmos",                 "cascode_current_mirror_tail_nmos"),
    ("differential_pair_nmos",            "telescopic_cascode_load_wideswing_nmos",       "cascode_current_mirror_tail_nmos"),
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
        "amplification_stage": modules["amplification_stage"],
        "output_stage":    modules["output_stage"],
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


# ─── two_stage single-ended round-trip (plain + buffered) ───────────────────
#
# Each combo carries an amplification-stage variant and an optional follower.
# The two source followers (common_drain/common_drain_nmos) moved to the
# output_stage category (issue #125) and can no longer be a gain stage, so
# every follower row targets two_stage_buffered_single_ended with the follower
# in its output_stage slot and a level-matched common-source amp stage in the
# second_stage slot; the follower-free rows keep the plain
# two_stage_opamp_single_ended topology.
#
# Combos cover all 3 compensation variants, all 4 real amplification/output
# stages, and all 5 input_pair variants across representative base
# combinations (current_source_load_* are pruned from single-ended topologies
# by load_branch_compatibility.py, issue #112).
# The stage-interface filter (second_stage_compatibility.py) restricts each
# tagged pair to the level-reachable stages: pmos pairs take
# common_source (amp) / common_drain (follower), nmos pairs take
# common_source_pmos / common_drain_nmos, inverter_based_input takes any.
# differential_ota_second_stage cannot appear here even via
# include_unsupported: in a 2-stage topology every compensation variant
# wraps it directly, and the compensation parity filter rejects a
# non-inverting stage with gain (issue #114) -- its round-trip coverage
# lives in the 3-stage RNMC combos.
# The constructed bias generator picks its stage-rail leg from the
# amplification/output stage (common_source/common_drain: gate_vdd;
# common_source_pmos/common_drain_nmos: gate_gnd) and its rail-7 leg from
# the tail's reference diode; see _ONE_STAGE_COMBOS comment.
_TWO_STAGE_COMBOS = [
    # fmt: off
    # input_pair                           load                                            tail_current                bias_gen  compensation                        amp_stage             follower
    ("differential_pair_pmos",             "telescopic_cascode_load_pmos",                 "current_mirror_tail_pmos", "miller_cap",                       "common_source",       None),
    ("differential_pair_pmos",             "resistor_load_gnd",                            "resistor_tail_vdd",        "miller_cap",                       "common_source",       "common_drain"),
    ("differential_pair_pmos",             "active_load_nmos",                             "current_mirror_tail_pmos", "miller_cap",                       "common_source",       "common_drain"),
    ("differential_pair_pmos",             "resistor_load_gnd",                            "resistor_tail_vdd",        "miller_cap_with_nulling_resistor", "common_source",       None),
    ("differential_pair_nmos",             "active_load_pmos",                             "current_mirror_tail_nmos", "miller_cap_with_nulling_resistor", "common_source_pmos",  "common_drain_nmos"),
    ("differential_pair_nmos",             "active_load_pmos",                             "resistor_tail_gnd",        "miller_cap_with_nulling_resistor", "common_source_pmos",  None),
    ("differential_pair_nmos",             "resistor_load_vdd",                            "resistor_tail_gnd",        "indirect_compensation",            "common_source_pmos",  None),
    ("differential_pair_nmos",             "telescopic_cascode_load_nmos",                 "resistor_tail_gnd",        "indirect_compensation",            "common_source_pmos",  "common_drain_nmos"),
    ("differential_pair_nmos_degenerated", "folded_cascode_load_nmos_input_single_output", "resistor_tail_gnd",        "indirect_compensation",            "common_source_pmos",  "common_drain_nmos"),
    ("differential_pair_pmos_degenerated", "folded_cascode_load_pmos_input_single_output", "resistor_tail_vdd",        "miller_cap",                       "common_source",       None),
    ("inverter_based_input",               "folded_cascode_load_pmos_input_single_output", _CANONICAL_TAIL,            "miller_cap_with_nulling_resistor", "common_source",       "common_drain"),
    # fmt: on
]


@pytest.fixture(scope="module")
def two_stage_fixtures():
    modules = load_modules()
    plain = next(t for t in load_topologies() if t.name == "two_stage_opamp_single_ended")
    buffered = next(t for t in load_topologies() if t.name == "two_stage_buffered_single_ended")
    return modules, plain, buffered


@pytest.mark.parametrize(
    "input_pair,load,tail_current,compensation,amp_stage,follower",
    _TWO_STAGE_COMBOS,
)
def test_round_trip_two_stage_opamp(
    two_stage_fixtures,
    input_pair, load, tail_current, compensation, amp_stage, follower,
):
    modules, plain, buffered = two_stage_fixtures
    topology = buffered if follower else plain
    # include_unsupported only for the parked inverter_based_input pair; the
    # followers are no longer parked.
    config = _INCLUDE_UNSUPPORTED if input_pair == "inverter_based_input" else {}
    simple_modules = {
        "input_pair":         [v for v in modules["input_pair"]         if v.name == input_pair],
        "load":               [v for v in modules["load"]               if v.name == load],
        "tail_current":       [v for v in modules["tail_current"]       if v.name == tail_current],
        "compensation":       [v for v in modules["compensation"]       if v.name == compensation],
        "amplification_stage":[v for v in modules["amplification_stage"] if v.name == amp_stage],
    }
    if follower:
        simple_modules["output_stage"] = [v for v in modules["output_stage"] if v.name == follower]
    circuit = next(enumerate_circuits(topology, simple_modules, config))

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
# 13 combos cover both cmfb variants, all 3 compensation variants on each of
# comp_p and comp_n independently (including asymmetric comps that
# exercise FBR's same-category disambiguation), the two enumerable
# amplification stages and the two followers (the stage-interface filter
# restricts pmos pairs to common_source (amp) / common_drain (follower) and
# nmos pairs to common_source_pmos / common_drain_nmos on both output paths;
# differential_ota_second_stage is excluded like in _TWO_STAGE_COMBOS --
# comp_p/comp_n wrap the stage directly, so the compensation parity filter
# rejects it, issue #114), and all 4
# input_pair variants reachable with differential-output
# loads (inverter_based_input excluded: it needs a neutral-load topology).
# Only the output_cardinality "differential" loads --
# folded_cascode_load_*_input_differential_output and the CMFB-driven
# current_source_load_* (issue #112) -- produce a real (non-absent) cmfb
# instance; all other loads get cmfb_absent and cannot test the cmfb
# patterns. The last 2 combos are the current_source_load_* round-trips
# (their only reachable templates are fully-differential ones).  A real cmfb makes rail 4 gate_gnd (its NMOS tail
# gate), so every FD-with-real-cmfb constructed generator mixes flavors and
# resolves to the constructed_bias pattern -- the consumer sets the retired
# flavor filter used to route to resistor_bias only (and whose
# current_mirror_tail_nmos pairings hit the historical B1 mis-recognition)
# are exactly the ones the per-leg construction and per-leg recognition now
# handle first-class.
# The follower rows (follower set) target two_stage_buffered_fully_differential
# with the follower in both output_stage_p/n slots and a level-matched CS amp
# in both second_stage_p/n slots; the follower-free rows keep the plain
# topology. The stage-interface filter forces both output paths onto the same
# amp (and the same follower) per pair polarity, so amp_stage/follower are
# single per row. Compensation asymmetry (comp_p != comp_n) is preserved to
# keep exercising FBR's same-category disambiguation.
_TWO_STAGE_FULLY_DIFF_COMBOS = [
    # fmt: off
    # input_pair                             load                                                  tail_current                         bias_gen                        cmfb                     comp_p                              comp_n                              amp_stage             follower
    ("differential_pair_pmos",              "folded_cascode_load_pmos_input_differential_output",  "current_mirror_tail_pmos",          "resistive_sense_cmfb",  "miller_cap",                       "miller_cap",                       "common_source",       None),
    ("differential_pair_pmos",              "folded_cascode_load_pmos_input_differential_output",  "resistor_tail_vdd",                 "dda_cmfb",              "miller_cap_with_nulling_resistor",  "miller_cap_with_nulling_resistor",  "common_source",       "common_drain"),
    ("differential_pair_pmos",              "folded_cascode_load_pmos_input_differential_output",  "cascode_current_mirror_tail_pmos",  "resistive_sense_cmfb",  "indirect_compensation",            "indirect_compensation",            "common_source",       None),
    ("differential_pair_pmos",              "folded_cascode_load_pmos_input_differential_output",  "current_mirror_tail_pmos",          "dda_cmfb",              "miller_cap",                       "miller_cap_with_nulling_resistor",  "common_source",       "common_drain"),
    ("differential_pair_nmos",              "folded_cascode_load_nmos_input_differential_output",  "resistor_tail_gnd",                 "resistive_sense_cmfb",  "miller_cap",                       "indirect_compensation",            "common_source_pmos",  None),
    ("differential_pair_nmos",              "folded_cascode_load_nmos_input_differential_output",  "resistor_tail_gnd",                 "dda_cmfb",              "indirect_compensation",            "miller_cap",                       "common_source_pmos",  "common_drain_nmos"),
    ("differential_pair_nmos",              "folded_cascode_load_nmos_input_differential_output",  "cascode_current_mirror_tail_nmos",  "resistive_sense_cmfb",  "miller_cap_with_nulling_resistor",  "indirect_compensation",            "common_source_pmos",  "common_drain_nmos"),
    ("differential_pair_nmos",              "folded_cascode_load_nmos_input_differential_output",  "resistor_tail_gnd",                 "dda_cmfb",              "miller_cap",                       "miller_cap_with_nulling_resistor",  "common_source_pmos",  None),
    ("differential_pair_pmos_degenerated",  "folded_cascode_load_pmos_input_differential_output",  "resistor_tail_vdd",                 "resistive_sense_cmfb",  "miller_cap_with_nulling_resistor",  "miller_cap_with_nulling_resistor",  "common_source",       None),
    ("differential_pair_nmos_degenerated",  "folded_cascode_load_nmos_input_differential_output",  "cascode_current_mirror_tail_nmos",  "dda_cmfb",              "indirect_compensation",            "indirect_compensation",            "common_source_pmos",  "common_drain_nmos"),
    ("differential_pair_pmos",              "folded_cascode_load_pmos_input_differential_output",  "resistor_tail_vdd",                 "dda_cmfb",              "indirect_compensation",            "indirect_compensation",            "common_source",       "common_drain"),
    ("differential_pair_pmos",              "current_source_load_nmos",                             "current_mirror_tail_pmos",          "resistive_sense_cmfb",  "miller_cap",                       "miller_cap",                       "common_source",       None),
    ("differential_pair_nmos",              "current_source_load_pmos",                             "current_mirror_tail_nmos",          "dda_cmfb",              "indirect_compensation",            "indirect_compensation",            "common_source_pmos",  None),
    # fmt: on
]


@pytest.fixture(scope="module")
def two_stage_fully_diff_fixtures():
    modules = load_modules()
    plain = next(t for t in load_topologies() if t.name == "two_stage_opamp_fully_differential")
    buffered = next(t for t in load_topologies() if t.name == "two_stage_buffered_fully_differential")
    return modules, plain, buffered


@pytest.mark.parametrize(
    "input_pair,load,tail_current,cmfb,comp_p,comp_n,amp_stage,follower",
    _TWO_STAGE_FULLY_DIFF_COMBOS,
)
def test_round_trip_two_stage_fully_diff(
    two_stage_fully_diff_fixtures,
    input_pair, load, tail_current, cmfb,
    comp_p, comp_n, amp_stage, follower,
):
    modules, plain, buffered = two_stage_fully_diff_fixtures
    topology = buffered if follower else plain
    simple_modules = {
        "input_pair":      [v for v in modules["input_pair"]      if v.name == input_pair],
        "load":            [v for v in modules["load"]            if v.name == load],
        "tail_current":    [v for v in modules["tail_current"]    if v.name == tail_current],
        "cmfb":            [v for v in modules["cmfb"]            if v.name == cmfb],
        # Both comp variants must be present so enumerate_circuits can fill
        # comp_p and comp_n independently; it maps by slot.category, not name.
        "compensation":    [v for v in modules["compensation"]    if v.name in (comp_p, comp_n)],
        "amplification_stage": [v for v in modules["amplification_stage"] if v.name == amp_stage],
    }
    if follower:
        simple_modules["output_stage"] = [v for v in modules["output_stage"] if v.name == follower]
    circuit = next(enumerate_circuits(topology, simple_modules))

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
# The plain 3-stage NMC topologies now enumerate ZERO: their comp1 wraps the
# second+third CS+CS cascade (non-inverting with gain, issue #114), and the
# followers that used to satisfy that outer loop moved to the output_stage
# category (issue #125) -- they can no longer be a gain stage. So plain NMC
# round-trips are unbuildable; NMC coverage now runs on the *buffered* NMC
# topologies, where the parked differential_ota_second_stage (opted in) can
# take the second_stage slot with a CS third stage (ota + CS = 3 inversions,
# the sign-correct nesting comp1 requires) and a follower fills output_stage.
#
# Each combo carries a target topology name plus ss/ts (the two amplification
# gain slots) and an optional follower (the output_stage slot, buffered
# topologies only). Follower rows use the buffered topology; the ota rows use
# the buffered NMC topology with include_unsupported; plain CS+CS rows use the
# plain RNMC topology (each RNMC compensation wraps a single inverting stage,
# so CS+CS is parity-legal, unlike NMC).
#
# The FBR assigned_ids mechanism (from #31) correctly handles the same-category
# gain slots via connectivity scoring on distinct nets.

_NMC_SE = "three_stage_buffered_nmc_single_ended"
_RNMC_SE_PLAIN = "three_stage_opamp_rnmc_single_ended"
_RNMC_SE_BUF = "three_stage_buffered_rnmc_single_ended"
_NMC_FD = "three_stage_buffered_nmc_fully_differential"
_RNMC_FD_PLAIN = "three_stage_opamp_rnmc_fully_differential"
_RNMC_FD_BUF = "three_stage_buffered_rnmc_fully_differential"

_THREE_STAGE_SE_COMBOS = [
    # (topology, input_pair, load, tail_current, ss, ts, follower, comp1, comp2)
    # Polarity rule: pmos input pair -> pmos_input-polarity loads
    # (active_load_nmos, folded_cascode_load_pmos_*, telescopic_cascode_load_pmos),
    # ss/ts common_source, follower common_drain; nmos input pair ->
    # nmos_input-polarity loads, ss/ts common_source_pmos, follower
    # common_drain_nmos. current_source_load_* are pruned from single-ended
    # topologies (issue #112). Covers all 3 comp variants, the CS/ota gain
    # stages and both followers, both polarities, degenerated pairs, several
    # load types.
    # Plain RNMC (CS ss + CS ts):
    (_RNMC_SE_PLAIN, "differential_pair_pmos", "active_load_nmos", "current_mirror_tail_pmos",
     "common_source", "common_source", None, "miller_cap", "miller_cap"),
    (_RNMC_SE_PLAIN, "differential_pair_nmos", "active_load_pmos", "resistor_tail_gnd",
     "common_source_pmos", "common_source_pmos", None, "indirect_compensation", "indirect_compensation"),
    (_RNMC_SE_PLAIN, "differential_pair_nmos", "active_load_pmos", "cascode_current_mirror_tail_nmos",
     "common_source_pmos", "common_source_pmos", None, "indirect_compensation", "miller_cap"),
    # Buffered RNMC (CS ss + CS ts + follower output_stage):
    (_RNMC_SE_BUF, "differential_pair_pmos", "resistor_load_gnd", "resistor_tail_vdd",
     "common_source", "common_source", "common_drain", "miller_cap", "indirect_compensation"),
    (_RNMC_SE_BUF, "differential_pair_nmos_degenerated", "active_load_pmos", "current_mirror_tail_nmos",
     "common_source_pmos", "common_source_pmos", "common_drain_nmos", "miller_cap_with_nulling_resistor", "miller_cap"),
    (_RNMC_SE_BUF, "differential_pair_pmos", "active_load_nmos", "resistor_tail_vdd",
     "common_source", "common_source", "common_drain", "miller_cap_with_nulling_resistor", "miller_cap_with_nulling_resistor"),
    # Buffered NMC (ota ss + CS ts + follower output_stage; ota is parked, so
    # include_unsupported -- ota + CS = 3 inversions is the parity-legal NMC
    # nesting comp1 requires):
    (_NMC_SE, "differential_pair_pmos", "folded_cascode_load_pmos_input_single_output", "cascode_current_mirror_tail_pmos",
     "differential_ota_second_stage", "common_source", "common_drain", "miller_cap_with_nulling_resistor", "indirect_compensation"),
    (_NMC_SE, "differential_pair_pmos", "telescopic_cascode_load_pmos", "resistor_tail_vdd",
     "differential_ota_second_stage", "common_source", "common_drain", "indirect_compensation", "miller_cap_with_nulling_resistor"),
]

_THREE_STAGE_FD_COMBOS = [
    # (topology, input_pair, load, tail_current, cmfb, ss, ts, follower, c1, c2)
    # Per-path ss/ts/follower are single per row (the stage-interface filter
    # forces both output paths onto the same variants for a given pair
    # polarity); compensation is symmetric here (c1/c2 on both paths).
    # Covers both cmfb variants, all 3 comp variants, CS/ota gain stages and
    # both followers, both polarities, degenerated pairs. Topology selection
    # mirrors the SE combos.
    # Plain RNMC FD (CS ss + CS ts):
    (_RNMC_FD_PLAIN, "differential_pair_pmos", "folded_cascode_load_pmos_input_differential_output",
     "current_mirror_tail_pmos", "resistive_sense_cmfb",
     "common_source", "common_source", None, "miller_cap", "miller_cap"),
    (_RNMC_FD_PLAIN, "differential_pair_nmos", "folded_cascode_load_nmos_input_differential_output",
     "cascode_current_mirror_tail_nmos", "resistive_sense_cmfb",
     "common_source_pmos", "common_source_pmos", None, "indirect_compensation", "indirect_compensation"),
    (_RNMC_FD_PLAIN, "differential_pair_pmos", "folded_cascode_load_pmos_input_differential_output",
     "resistor_tail_vdd", "dda_cmfb",
     "common_source", "common_source", None, "miller_cap", "indirect_compensation"),
    # Buffered RNMC FD (CS ss + CS ts + follower output_stage):
    (_RNMC_FD_BUF, "differential_pair_nmos", "folded_cascode_load_nmos_input_differential_output",
     "cascode_current_mirror_tail_nmos", "dda_cmfb",
     "common_source_pmos", "common_source_pmos", "common_drain_nmos", "miller_cap_with_nulling_resistor", "miller_cap_with_nulling_resistor"),
    (_RNMC_FD_BUF, "differential_pair_pmos_degenerated", "folded_cascode_load_pmos_input_differential_output",
     "resistor_tail_vdd", "resistive_sense_cmfb",
     "common_source", "common_source", "common_drain", "miller_cap_with_nulling_resistor", "miller_cap_with_nulling_resistor"),
    (_RNMC_FD_BUF, "differential_pair_nmos", "folded_cascode_load_nmos_input_differential_output",
     "resistor_tail_gnd", "resistive_sense_cmfb",
     "common_source_pmos", "common_source_pmos", "common_drain_nmos", "miller_cap", "miller_cap"),
    # Buffered NMC FD (ota ss + CS ts + follower output_stage; include_unsupported):
    (_NMC_FD, "differential_pair_pmos", "folded_cascode_load_pmos_input_differential_output",
     "resistor_tail_vdd", "dda_cmfb",
     "differential_ota_second_stage", "common_source", "common_drain",
     "miller_cap_with_nulling_resistor", "miller_cap_with_nulling_resistor"),
]


@pytest.fixture(scope="module")
def three_stage_topos():
    modules = load_modules()
    topos = {t.name: t for t in load_topologies()}
    return modules, topos


def _run_three_stage_se(modules, topology, input_pair, load, tail_current,
                        ss, ts, follower, comp1, comp2, config):
    simple_modules = {
        "input_pair":      [v for v in modules["input_pair"]      if v.name == input_pair],
        "load":            [v for v in modules["load"]            if v.name == load],
        "tail_current":    [v for v in modules["tail_current"]    if v.name == tail_current],
        # second_stage + third_stage slots both have category amplification_stage.
        "amplification_stage": [v for v in modules["amplification_stage"] if v.name in (ss, ts)],
        # comp1 and comp2 both have category 'compensation'.
        "compensation":    [v for v in modules["compensation"]    if v.name in (comp1, comp2)],
    }
    if follower:
        simple_modules["output_stage"] = [v for v in modules["output_stage"] if v.name == follower]
    circuit = next(enumerate_circuits(topology, simple_modules, config))
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
                        ss, ts, follower, c1, c2, config):
    simple_modules = {
        "input_pair":      [v for v in modules["input_pair"]      if v.name == input_pair],
        "load":            [v for v in modules["load"]            if v.name == load],
        "tail_current":    [v for v in modules["tail_current"]    if v.name == tail_current],
        "cmfb":            [v for v in modules["cmfb"]            if v.name == cmfb],
        # 4 amplification_stage slots (ss_p, ts_p, ss_n, ts_n) draw from this pool.
        "amplification_stage": [v for v in modules["amplification_stage"] if v.name in (ss, ts)],
        # 4 compensation-category slots draw from this pool.
        "compensation":    [v for v in modules["compensation"]    if v.name in (c1, c2)],
    }
    if follower:
        simple_modules["output_stage"] = [v for v in modules["output_stage"] if v.name == follower]
    circuit = next(enumerate_circuits(topology, simple_modules, config))
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
    "topo_name,input_pair,load,tail_current,ss,ts,follower,comp1,comp2",
    _THREE_STAGE_SE_COMBOS,
)
def test_round_trip_three_stage_se(
    three_stage_topos,
    topo_name, input_pair, load, tail_current, ss, ts, follower, comp1, comp2,
):
    modules, topos = three_stage_topos
    topology = topos[topo_name]
    # include_unsupported only where differential_ota_second_stage appears.
    config = _INCLUDE_UNSUPPORTED if ss == "differential_ota_second_stage" else {}
    _run_three_stage_se(modules, topology, input_pair, load, tail_current,
                        ss, ts, follower, comp1, comp2, config)


@pytest.mark.parametrize(
    "topo_name,input_pair,load,tail_current,cmfb,ss,ts,follower,c1,c2",
    _THREE_STAGE_FD_COMBOS,
)
def test_round_trip_three_stage_fd(
    three_stage_topos,
    topo_name, input_pair, load, tail_current, cmfb, ss, ts, follower, c1, c2,
):
    modules, topos = three_stage_topos
    topology = topos[topo_name]
    config = _INCLUDE_UNSUPPORTED if ss == "differential_ota_second_stage" else {}
    _run_three_stage_fd(modules, topology, input_pair, load, tail_current, cmfb,
                        ss, ts, follower, c1, c2, config)
