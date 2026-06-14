import pytest
from circuitgenome.synthesizer.bias_pruning import needed_bias_outputs, prune_bias_generation
from circuitgenome.synthesizer.cmfb_compatibility import CANONICAL_CMFB_VARIANT, is_cmfb_compatible, prune_cmfb
from circuitgenome.synthesizer.compatibility import is_combination_valid
from circuitgenome.synthesizer.output_compatibility import is_output_type_compatible
from circuitgenome.synthesizer.tail_current_compatibility import (
    CANONICAL_TAIL_CURRENT_VARIANT,
    is_tail_current_compatible,
    prune_tail_current,
)
from circuitgenome.synthesizer.loader import load_modules, load_topologies
from circuitgenome.synthesizer.synthesizer import enumerate_circuits, synthesize
from circuitgenome.synthesizer.netlist import to_flat_spice, to_hierarchical_spice


def test_load_modules():
    modules = load_modules()
    assert "input_pair" in modules
    assert "load" in modules
    assert "tail_current" in modules
    assert "bias_generation" in modules
    assert "compensation" in modules
    assert "second_stage" in modules
    # Spot-check a known variant
    names = [v.name for v in modules["input_pair"]]
    assert "differential_pair_pmos" in names


def test_load_variant_names():
    """The load category exposes 12 variants: alias-based simple loads (each
    split into a VDD-side and a GND-side variant, since a PMOS-input pair and
    an NMOS-input pair can't draw current from the same rail), plus
    PMOS/NMOS-input single-output and differential-output folded-cascode
    loads, plus PMOS/NMOS telescopic-cascode loads."""
    modules = load_modules()
    names = {v.name for v in modules["load"]}
    assert names == {
        "resistor_load_vdd",
        "resistor_load_gnd",
        "active_load_pmos",
        "active_load_nmos",
        "current_source_load_pmos",
        "current_source_load_nmos",
        "folded_cascode_load_nmos_input_single_output",
        "folded_cascode_load_pmos_input_single_output",
        "folded_cascode_load_nmos_input_differential_output",
        "folded_cascode_load_pmos_input_differential_output",
        "telescopic_cascode_load_pmos",
        "telescopic_cascode_load_nmos",
    }


def test_load_ports_identical_across_variants():
    """Every load variant declares the same canonical 11-port signature, in
    the same order — only the per-port `role` (and `alias_of`) differs."""
    modules = load_modules()
    canonical = [
        "in1", "in2", "out", "out1", "out2",
        "bias1", "bias2", "bias3", "bias_cmfb", "vdd", "gnd",
    ]
    for variant in modules["load"]:
        names = [p.name for p in variant.ports]
        assert names == canonical, f"{variant.name}: {names}"


def test_cascode_loads_do_not_use_signal_nodes_as_bias():
    """Folded-cascode and telescopic-cascode loads must not reuse the
    in1/in2/out/out1/out2 signal nodes as gate/bias references."""
    modules = load_modules()
    cascode_variants = [
        v for v in modules["load"]
        if v.name.startswith(("folded_cascode_load", "telescopic_cascode_load"))
    ]
    assert len(cascode_variants) == 6
    for variant in cascode_variants:
        for device in variant.devices:
            gate = device.terminals.get("g")
            assert gate not in ("in1", "in2", "out", "out1", "out2"), (
                f"{variant.name}.{device.ref}: gate tied to signal node {gate!r}"
            )


def test_folded_cascode_bias_port_roles():
    """Single-output folded-cascode loads require bias1+bias2 (bias3
    optional); telescopic-cascode loads require only bias1 (bias2/bias3
    optional); differential-output folded-cascode loads require
    bias1+bias2+bias3+bias_cmfb."""
    modules = load_modules()
    by_name = {v.name: v for v in modules["load"]}

    for name in (
        "folded_cascode_load_nmos_input_single_output",
        "folded_cascode_load_pmos_input_single_output",
    ):
        roles = {p.name: p.role for p in by_name[name].ports}
        assert roles["bias1"] == "input"
        assert roles["bias2"] == "input"
        assert roles["bias3"] == "optional"
        assert roles["bias_cmfb"] == "optional"

    for name in (
        "telescopic_cascode_load_pmos",
        "telescopic_cascode_load_nmos",
    ):
        roles = {p.name: p.role for p in by_name[name].ports}
        assert roles["bias1"] == "input"
        assert roles["bias2"] == "optional"
        assert roles["bias3"] == "optional"
        assert roles["bias_cmfb"] == "optional"

    for name in (
        "folded_cascode_load_nmos_input_differential_output",
        "folded_cascode_load_pmos_input_differential_output",
    ):
        roles = {p.name: p.role for p in by_name[name].ports}
        assert roles["bias1"] == "input"
        assert roles["bias2"] == "input"
        assert roles["bias3"] == "input"
        assert roles["bias_cmfb"] == "input"


def test_synthesize_differential_output_folded_cascode_wires_distinct_bias_rails():
    """bias1/bias2/bias3 of a differential-output folded-cascode load each
    resolve to a distinct net_bias rail (no floating gates, no accidental
    rail sharing); bias_cmfb resolves to net_cmfb_out, driven by the cmfb
    module's output."""
    modules = load_modules()
    topologies = load_topologies()
    topo = next(t for t in topologies if t.name == "two_stage_opamp_fully_differential")

    simple_modules = {
        "input_pair": [v for v in modules["input_pair"] if v.name == "differential_pair_nmos"],
        "load": [v for v in modules["load"] if v.name == "folded_cascode_load_nmos_input_differential_output"],
        "tail_current": [v for v in modules["tail_current"] if v.name == "current_mirror_tail_nmos"],
        "bias_generation": [v for v in modules["bias_generation"] if v.name == "diode_connected_mosfet_bias"],
        "cmfb": [v for v in modules["cmfb"] if v.name == "resistive_sense_cmfb"],
        "compensation": [v for v in modules["compensation"] if v.name == "miller_cap"],
        "second_stage": [v for v in modules["second_stage"] if v.name == "common_source"],
    }

    circuit = next(enumerate_circuits(topo, simple_modules))
    load_devices = {ref: dev for ref, dev in circuit.devices if ref.startswith("load_")}

    assert len(load_devices) == 8
    bias_gates = {dev.terminals["g"] for dev in load_devices.values()}
    assert bias_gates == {"net_bias1", "net_bias2", "net_bias3", "net_cmfb_out"}


def test_tail_current_variant_names():
    """The tail_current category exposes 6 variants: each implementation
    (current mirror, cascode current mirror, resistor) comes in a PMOS/VDD-side
    flavor (for PMOS input pairs, whose tail sources current down from vdd)
    and an NMOS/GND-side flavor (for NMOS input pairs, whose tail sinks
    current to gnd)."""
    modules = load_modules()
    names = {v.name for v in modules["tail_current"]}
    assert names == {
        "current_mirror_tail_pmos",
        "current_mirror_tail_nmos",
        "cascode_current_mirror_tail_pmos",
        "cascode_current_mirror_tail_nmos",
        "resistor_tail_vdd",
        "resistor_tail_gnd",
    }


def test_cmfb_variant_names_and_ports():
    """The cmfb category exposes 2 variants (resistive-sense 5T OTA and
    differential-difference amplifier), both sharing the canonical
    in1/in2/vref/bias/out/vdd/gnd port signature and untagged for polarity/
    output_cardinality (compatible with any combination)."""
    modules = load_modules()
    names = {v.name for v in modules["cmfb"]}
    assert names == {"resistive_sense_cmfb", "dda_cmfb"}

    for variant in modules["cmfb"]:
        port_names = [p.name for p in variant.ports]
        assert port_names == ["in1", "in2", "vref", "bias", "out", "vdd", "gnd"], variant.name
        assert variant.polarity is None
        assert variant.output_cardinality is None


def test_bias_generation_variants_share_uniform_leg_structure():
    """All three bias_generation variants declare ibias/out1-7/vdd/gnd ports
    and 15 devices: 1 shared reference device (terminals never reference
    out1..out7) plus 7 legs of 2 devices each (both referencing the same
    outN)."""
    modules = load_modules()
    for variant in modules["bias_generation"]:
        port_names = [p.name for p in variant.ports]
        assert port_names == [
            "ibias", "out1", "out2", "out3", "out4", "out5", "out6", "out7", "vdd", "gnd",
        ], variant.name
        assert len(variant.devices) == 15, variant.name

        shared = [
            dev for dev in variant.devices
            if not any(t.startswith("out") for t in dev.terminals.values())
        ]
        assert len(shared) == 1, variant.name

        for i in range(1, 8):
            rail = f"out{i}"
            leg = [dev for dev in variant.devices if rail in dev.terminals.values()]
            assert len(leg) == 2, f"{variant.name}: leg for {rail}"
            for dev in leg:
                refs = {t for t in dev.terminals.values() if t.startswith("out")}
                assert refs == {rail}, f"{variant.name}.{dev.ref}: {refs}"


def test_polarity_tags_cover_input_pair_load_tail_current():
    """input_pair, load, and tail_current variants are split into pmos_input
    and nmos_input polarity groups (except inverter_based_input, which has no
    current-direction requirement); bias_generation variants are untagged
    (compatible with either polarity)."""
    modules = load_modules()

    input_pair_polarities = {v.name: v.polarity for v in modules["input_pair"]}
    assert input_pair_polarities["inverter_based_input"] is None
    for name in ("differential_pair_pmos", "differential_pair_pmos_degenerated"):
        assert input_pair_polarities[name] == "pmos_input"
    for name in ("differential_pair_nmos", "differential_pair_nmos_degenerated"):
        assert input_pair_polarities[name] == "nmos_input"

    load_polarities = [v.polarity for v in modules["load"]]
    assert load_polarities.count("pmos_input") == 6
    assert load_polarities.count("nmos_input") == 6

    tail_polarities = [v.polarity for v in modules["tail_current"]]
    assert tail_polarities.count("pmos_input") == 3
    assert tail_polarities.count("nmos_input") == 3

    assert all(v.polarity is None for v in modules["bias_generation"])


def test_is_combination_valid_denies_polarity_mismatches():
    """differential_pair_nmos (drains out1/out2 into the tail) can't pair with
    active_load_nmos (which also sinks out1/out2 to gnd) or
    current_mirror_tail_pmos (which also sources current into the tail) --
    both leave a node with no DC current path. The mirror-image pmos/vdd
    pairing is invalid for the same reason."""
    modules = load_modules()
    by_name = {v.name: v for cat in modules.values() for v in cat}

    bad_combos = [
        {"input_pair": "differential_pair_nmos", "load": "active_load_nmos", "tail_current": "current_mirror_tail_nmos"},
        {"input_pair": "differential_pair_nmos", "load": "active_load_pmos", "tail_current": "current_mirror_tail_pmos"},
        {"input_pair": "differential_pair_pmos", "load": "resistor_load_vdd", "tail_current": "resistor_tail_vdd"},
    ]
    for combo in bad_combos:
        variant_map = {slot: by_name[name] for slot, name in combo.items()}
        assert not is_combination_valid(variant_map), combo

    good_combo = {
        "input_pair": by_name["differential_pair_nmos"],
        "load": by_name["active_load_pmos"],
        "tail_current": by_name["current_mirror_tail_nmos"],
    }
    assert is_combination_valid(good_combo)


def test_inverter_based_input_compatible_with_every_load_and_tail():
    """inverter_based_input has no polarity tag, so it has no
    current-direction requirement: it's valid alongside every load x
    tail_current combination, including ones that mismatch each other's
    polarity tags."""
    modules = load_modules()
    by_name = {v.name: v for cat in modules.values() for v in cat}
    input_pair = by_name["inverter_based_input"]

    for load in modules["load"]:
        for tail in modules["tail_current"]:
            variant_map = {"input_pair": input_pair, "load": load, "tail_current": tail}
            assert is_combination_valid(variant_map), (load.name, tail.name)


def test_enumerate_circuits_excludes_polarity_mismatches():
    """Every synthesized 2-stage single-ended circuit has a load and
    tail_current whose polarity tag (if any) matches its input_pair's
    polarity tag (if any)."""
    modules = load_modules()
    topologies = load_topologies()
    topo = next(t for t in topologies if t.name == "two_stage_opamp_single_ended")

    for circuit in enumerate_circuits(topo, modules):
        input_pair = circuit.variant_map["input_pair"]
        if input_pair.polarity is None:
            continue
        for slot_name in ("load", "tail_current"):
            variant = circuit.variant_map[slot_name]
            assert variant.polarity in (None, input_pair.polarity), (
                f"{circuit.name}: input_pair={input_pair.name} "
                f"({input_pair.polarity}) vs {slot_name}={variant.name} "
                f"({variant.polarity})"
            )


def test_output_cardinality_tags_cover_folded_and_telescopic_cascode_loads():
    """Single-output folded-cascode and telescopic-cascode loads declare a
    mandatory `out` (wired only in single_ended topologies) and are tagged
    output_cardinality "single"; differential-output folded-cascode loads
    declare mandatory `out1`/`out2` (wired only in fully_differential
    topologies) and are tagged "differential". The other 6 loads
    (resistor/active/current-source) have no such mandatory port and are
    untagged (compatible with either output type)."""
    modules = load_modules()
    cardinalities = {v.name: v.output_cardinality for v in modules["load"]}

    for name in (
        "folded_cascode_load_nmos_input_single_output",
        "folded_cascode_load_pmos_input_single_output",
        "telescopic_cascode_load_pmos",
        "telescopic_cascode_load_nmos",
    ):
        assert cardinalities[name] == "single", name

    for name in (
        "folded_cascode_load_nmos_input_differential_output",
        "folded_cascode_load_pmos_input_differential_output",
    ):
        assert cardinalities[name] == "differential", name

    for name in (
        "resistor_load_vdd",
        "resistor_load_gnd",
        "active_load_pmos",
        "active_load_nmos",
        "current_source_load_pmos",
        "current_source_load_nmos",
    ):
        assert cardinalities[name] is None, name


def test_is_output_type_compatible_denies_cardinality_mismatches():
    """A differential-output folded-cascode load (output_cardinality
    "differential") would leave its mandatory out1/out2 ports floating in a
    single_ended topology (no net_loadout1/net_loadout2 defined there); a
    single-output folded-cascode load (output_cardinality "single") would
    leave its mandatory out port floating in a fully_differential topology.
    Both are rejected."""
    modules = load_modules()
    topologies = load_topologies()
    by_name = {v.name: v for v in modules["load"]}
    se_topo = next(t for t in topologies if t.name == "one_stage_opamp")
    fd_topo = next(t for t in topologies if t.name == "two_stage_opamp_fully_differential")

    diff_load = by_name["folded_cascode_load_nmos_input_differential_output"]
    single_load = by_name["folded_cascode_load_nmos_input_single_output"]

    assert not is_output_type_compatible(se_topo, {"load": diff_load})
    assert not is_output_type_compatible(fd_topo, {"load": single_load})


def test_is_output_type_compatible_allows_matches_and_untagged():
    """A differential-output load matches a fully_differential topology, a
    single-output load matches a single_ended topology, and an untagged load
    (output_cardinality None) is compatible with either."""
    modules = load_modules()
    topologies = load_topologies()
    by_name = {v.name: v for v in modules["load"]}
    se_topo = next(t for t in topologies if t.name == "one_stage_opamp")
    fd_topo = next(t for t in topologies if t.name == "two_stage_opamp_fully_differential")

    diff_load = by_name["folded_cascode_load_nmos_input_differential_output"]
    single_load = by_name["folded_cascode_load_nmos_input_single_output"]
    untagged_load = by_name["resistor_load_gnd"]

    assert is_output_type_compatible(fd_topo, {"load": diff_load})
    assert is_output_type_compatible(se_topo, {"load": single_load})
    assert is_output_type_compatible(se_topo, {"load": untagged_load})
    assert is_output_type_compatible(fd_topo, {"load": untagged_load})


def test_enumerate_circuits_excludes_output_cardinality_mismatches():
    """Every synthesized circuit's load has an output_cardinality compatible
    with its topology's output_type: a differential-output cascode load never
    appears in a single_ended circuit, and a single-output cascode or
    telescopic load never appears in a fully_differential circuit."""
    modules = load_modules()
    topologies = load_topologies()

    se_topo = next(t for t in topologies if t.name == "two_stage_opamp_single_ended")
    for circuit in enumerate_circuits(se_topo, modules):
        assert circuit.variant_map["load"].output_cardinality != "differential"

    fd_topo = next(t for t in topologies if t.name == "two_stage_opamp_fully_differential")
    for circuit in enumerate_circuits(fd_topo, modules):
        assert circuit.variant_map["load"].output_cardinality != "single"


def test_is_cmfb_compatible_differential_load_allows_both_cmfb_variants():
    """A load with output_cardinality "differential" has a real bias_cmfb
    consumer (folded_cascode_load_*_input_differential_output's mn3/mn4 or
    mp1/mp2), so either cmfb variant produces a meaningfully different
    circuit -- both are compatible."""
    modules = load_modules()
    diff_load = next(v for v in modules["load"] if v.name == "folded_cascode_load_nmos_input_differential_output")

    for cmfb_variant in modules["cmfb"]:
        assert is_cmfb_compatible({"load": diff_load, "cmfb": cmfb_variant})


def test_is_cmfb_compatible_other_loads_only_allow_canonical_variant():
    """A load with output_cardinality None declares bias_cmfb as optional and
    never references it, so cmfb.out drives nothing -- only
    CANONICAL_CMFB_VARIANT is allowed through, to avoid enumerating the other
    cmfb variant as a duplicate no-op circuit."""
    modules = load_modules()
    untagged_load = next(v for v in modules["load"] if v.name == "resistor_load_gnd")
    cmfb_by_name = {v.name: v for v in modules["cmfb"]}

    assert is_cmfb_compatible({"load": untagged_load, "cmfb": cmfb_by_name[CANONICAL_CMFB_VARIANT]})
    for name, variant in cmfb_by_name.items():
        if name == CANONICAL_CMFB_VARIANT:
            continue
        assert not is_cmfb_compatible({"load": untagged_load, "cmfb": variant})


def test_is_cmfb_compatible_topology_without_cmfb_slot():
    """A variant_map with no "cmfb" key (single_ended topologies have no cmfb
    slot) is always compatible -- the filter is a no-op."""
    modules = load_modules()
    untagged_load = next(v for v in modules["load"] if v.name == "resistor_load_gnd")
    assert is_cmfb_compatible({"load": untagged_load})


def test_prune_cmfb_keeps_variant_for_differential_load():
    """A load with output_cardinality "differential" has a real bias_cmfb
    consumer -- the cmfb variant is returned unchanged."""
    modules = load_modules()
    diff_load = next(v for v in modules["load"] if v.name == "folded_cascode_load_nmos_input_differential_output")
    cmfb_variant = next(v for v in modules["cmfb"] if v.name == CANONICAL_CMFB_VARIANT)

    assert prune_cmfb(cmfb_variant, diff_load) is cmfb_variant


def test_prune_cmfb_empties_variant_for_other_loads():
    """A load with output_cardinality None has no bias_cmfb consumer -- the
    cmfb variant is replaced with an empty placeholder (no ports, no
    devices), so it contributes nothing to the assembled circuit and
    cmfb.bias is no longer "needed" by needed_bias_outputs."""
    modules = load_modules()
    untagged_load = next(v for v in modules["load"] if v.name == "resistor_load_gnd")
    cmfb_variant = next(v for v in modules["cmfb"] if v.name == CANONICAL_CMFB_VARIANT)

    pruned = prune_cmfb(cmfb_variant, untagged_load)
    assert pruned.name == "cmfb_absent"
    assert pruned.ports == []
    assert pruned.devices == []


def test_enumerate_circuits_cmfb_present_iff_differential_load():
    """For every synthesized fully-differential circuit, the cmfb slot's
    variant has devices iff the load's output_cardinality is "differential" --
    otherwise cmfb is pruned to an empty placeholder and no cmfb_* devices
    appear in the assembled circuit."""
    modules = load_modules()
    topo = next(t for t in load_topologies() if t.name == "two_stage_opamp_fully_differential")

    for circuit in enumerate_circuits(topo, modules):
        is_differential = circuit.variant_map["load"].output_cardinality == "differential"
        assert bool(circuit.variant_map["cmfb"].devices) == is_differential
        cmfb_device_refs = [ref for ref, _ in circuit.devices if ref.startswith("cmfb_")]
        assert bool(cmfb_device_refs) == is_differential


def test_is_tail_current_compatible_tail_consuming_input_pair_allows_all_variants():
    """differential_pair_pmos references its tail port (s/b: tail on the tail
    transistor), so every tail_current variant supplies a real bias current
    -- all 6 are compatible."""
    modules = load_modules()
    by_name = {v.name: v for cat in modules.values() for v in cat}
    input_pair = by_name["differential_pair_pmos"]

    for tail_variant in modules["tail_current"]:
        assert is_tail_current_compatible({"input_pair": input_pair, "tail_current": tail_variant})


def test_is_tail_current_compatible_inverter_based_input_only_allows_canonical_variant():
    """inverter_based_input never references its tail port, so
    tail_current.out drives nothing -- only CANONICAL_TAIL_CURRENT_VARIANT is
    allowed through, to avoid enumerating the other 5 tail_current variants as
    duplicate no-op circuits."""
    modules = load_modules()
    by_name = {v.name: v for cat in modules.values() for v in cat}
    input_pair = by_name["inverter_based_input"]
    tail_by_name = {v.name: v for v in modules["tail_current"]}

    assert is_tail_current_compatible(
        {"input_pair": input_pair, "tail_current": tail_by_name[CANONICAL_TAIL_CURRENT_VARIANT]}
    )
    for name, variant in tail_by_name.items():
        if name == CANONICAL_TAIL_CURRENT_VARIANT:
            continue
        assert not is_tail_current_compatible({"input_pair": input_pair, "tail_current": variant})


def test_prune_tail_current_keeps_variant_for_tail_consuming_input_pair():
    """differential_pair_pmos references its tail port -- the tail_current
    variant is returned unchanged."""
    modules = load_modules()
    by_name = {v.name: v for cat in modules.values() for v in cat}
    input_pair = by_name["differential_pair_pmos"]
    tail_variant = by_name[CANONICAL_TAIL_CURRENT_VARIANT]

    assert prune_tail_current(tail_variant, input_pair) is tail_variant


def test_prune_tail_current_empties_variant_for_inverter_based_input():
    """inverter_based_input never references its tail port -- the
    tail_current variant is replaced with an empty placeholder (no ports, no
    devices), so it contributes nothing to the assembled circuit and
    tail_current.bias is no longer "needed" by needed_bias_outputs."""
    modules = load_modules()
    by_name = {v.name: v for cat in modules.values() for v in cat}
    input_pair = by_name["inverter_based_input"]
    tail_variant = by_name[CANONICAL_TAIL_CURRENT_VARIANT]

    pruned = prune_tail_current(tail_variant, input_pair)
    assert pruned.name == "tail_current_absent"
    assert pruned.ports == []
    assert pruned.devices == []


def test_enumerate_circuits_tail_current_present_iff_not_inverter_based_input():
    """For every synthesized circuit, the tail_current slot's variant has
    devices iff input_pair is not inverter_based_input -- for
    inverter_based_input circuits, tail_current is pruned to an empty
    placeholder, no tail_current_* devices appear, net_tail is never a device
    terminal (no longer floating), and bias_gen has no rail-7 leg (out7),
    closing out Issue #17."""
    modules = load_modules()
    topo = next(t for t in load_topologies() if t.name == "one_stage_opamp")

    for circuit in enumerate_circuits(topo, modules):
        is_inverter_based = circuit.variant_map["input_pair"].name == "inverter_based_input"
        assert bool(circuit.variant_map["tail_current"].devices) != is_inverter_based

        tail_current_device_refs = [ref for ref, _ in circuit.devices if ref.startswith("tail_current_")]
        assert bool(tail_current_device_refs) != is_inverter_based

        if is_inverter_based:
            assert circuit.variant_map["tail_current"].name == "tail_current_absent"

            all_terms = {t for _, dev in circuit.devices for t in dev.terminals.values()}
            assert "net_tail" not in all_terms

            bias_variant = circuit.variant_map["bias_gen"]
            assert "out7" not in {p.name for p in bias_variant.ports}


def test_load_topologies():
    topologies = load_topologies()
    names = [t.name for t in topologies]
    assert "two_stage_opamp_single_ended" in names
    assert "one_stage_opamp" in names
    assert "three_stage_opamp_nmc_single_ended" in names
    assert "three_stage_opamp_rnmc_single_ended" in names
    assert "three_stage_opamp_nmc_fully_differential" in names
    assert "three_stage_opamp_rnmc_fully_differential" in names


def test_enumerate_circuits_nonempty():
    modules = load_modules()
    topologies = load_topologies()
    topo = next(t for t in topologies if t.name == "two_stage_opamp_single_ended")
    circuits = list(enumerate_circuits(topo, modules))
    assert len(circuits) > 0


def test_enumerate_circuits_count():
    """2-stage single-ended: of the 5 input pairs x 12 loads x 6 tails = 360
    input_pair/load/tail_current combinations, 144 are polarity-valid (see
    test_polarity_filter_*). is_tail_current_compatible then collapses the 72
    inverter_based_input combinations' 6 tail_current choices down to 1 (72 ->
    12), for 84 effective combinations -- of those, 70 also have an
    output_cardinality compatible with single_ended (the 14
    "differential"-cardinality combos are excluded; see
    test_is_output_type_compatible_*) x 3 bias x 3 comp x 3 second = 1890."""
    modules = load_modules()
    topologies = load_topologies()
    topo = next(t for t in topologies if t.name == "two_stage_opamp_single_ended")
    circuits = list(enumerate_circuits(topo, modules))
    assert len(circuits) == 1890


def test_enumerate_circuits_fully_differential_count():
    """2-stage fully-differential: of the 84 effective input_pair/load/
    tail_current combinations (144 polarity-valid, collapsed to 84 by
    is_tail_current_compatible -- see test_enumerate_circuits_count), 56 have
    an output_cardinality compatible with fully_differential (the 28
    "single"-cardinality combos are excluded). Of those 56, 14 use a
    "differential"-cardinality load -- the only loads with a real bias_cmfb
    consumer -- and keep both cmfb variants (14 x 2 = 28); the other 42 have
    no bias_cmfb consumer, so is_cmfb_compatible collapses cmfb to 1 canonical
    variant (42 x 1 = 42). 28 + 42 = 70 effective load/cmfb combinations, x 3
    bias x (3 comp x 3 second)^2 = 70 x 3^5 = 17010."""
    modules = load_modules()
    topologies = load_topologies()
    topo = next(t for t in topologies if t.name == "two_stage_opamp_fully_differential")
    circuits = list(enumerate_circuits(topo, modules))
    assert len(circuits) == 17010


def test_flat_spice_structure():
    modules = load_modules()
    topologies = load_topologies()
    topo = next(t for t in topologies if t.name == "one_stage_opamp")

    # Use the simplest variants for a deterministic test
    simple_modules = {
        "input_pair": [v for v in modules["input_pair"] if v.name == "differential_pair_pmos"],
        "load": [v for v in modules["load"] if v.name == "resistor_load_gnd"],
        "tail_current": [v for v in modules["tail_current"] if v.name == "resistor_tail_vdd"],
        "bias_generation": [v for v in modules["bias_generation"] if v.name == "resistor_bias"],
    }

    circuit = next(enumerate_circuits(topo, simple_modules))
    spice = to_flat_spice(circuit, name="test_opamp")

    assert spice.startswith(".subckt test_opamp")
    assert spice.endswith(".ends")
    # Should have the external ports in the header
    for port in topo.external_ports:
        assert port in spice.split("\n")[0]


def test_flat_spice_has_devices():
    modules = load_modules()
    topologies = load_topologies()
    topo = next(t for t in topologies if t.name == "one_stage_opamp")

    simple_modules = {
        "input_pair": [v for v in modules["input_pair"] if v.name == "differential_pair_pmos"],
        "load": [v for v in modules["load"] if v.name == "resistor_load_gnd"],
        "tail_current": [v for v in modules["tail_current"] if v.name == "resistor_tail_vdd"],
        "bias_generation": [v for v in modules["bias_generation"] if v.name == "resistor_bias"],
    }

    circuit = next(enumerate_circuits(topo, simple_modules))
    spice = to_flat_spice(circuit)
    lines = [l for l in spice.split("\n") if l and not l.startswith(".")]
    assert len(lines) > 0


def test_hierarchical_spice_has_subckt_definitions():
    modules = load_modules()
    topologies = load_topologies()
    topo = next(t for t in topologies if t.name == "one_stage_opamp")

    simple_modules = {
        "input_pair": [v for v in modules["input_pair"] if v.name == "differential_pair_pmos"],
        "load": [v for v in modules["load"] if v.name == "resistor_load_gnd"],
        "tail_current": [v for v in modules["tail_current"] if v.name == "resistor_tail_vdd"],
        "bias_generation": [v for v in modules["bias_generation"] if v.name == "resistor_bias"],
    }

    circuit = next(enumerate_circuits(topo, simple_modules))
    spice = to_hierarchical_spice(circuit, name="test_opamp_hier")

    # Should have module subcircuit definitions
    assert ".subckt differential_pair_pmos" in spice
    assert ".subckt resistor_load_gnd" in spice
    # Should have top-level definition
    assert ".subckt test_opamp_hier" in spice
    # Should have X-instances
    assert "Xinput_pair" in spice


def test_synthesize_api():
    circuits = synthesize({"stages": 1, "output_type": "single_ended"})
    assert len(circuits) > 0
    # All circuits should come from the one_stage_opamp topology
    for c in circuits:
        assert c.topology == "one_stage_opamp"


def test_synthesize_topology_filter():
    circuits = synthesize({"topology": "two_stage_opamp_single_ended"})
    assert len(circuits) > 0
    for c in circuits:
        assert c.topology == "two_stage_opamp_single_ended"


def test_enumerate_three_stage_single_ended_count():
    """3-stage single-ended (NMC/RNMC): 70 polarity-and-output_cardinality-valid
    input_pair/load/tail_current combinations (see test_enumerate_circuits_count)
    x 3 bias x 3 second stages x 3 third stages x 3 comp1 x 3 comp2 = 17010."""
    modules = load_modules()
    topologies = load_topologies()
    for name in ("three_stage_opamp_nmc_single_ended", "three_stage_opamp_rnmc_single_ended"):
        topo = next(t for t in topologies if t.name == name)
        circuits = list(enumerate_circuits(topo, modules))
        assert len(circuits) == 17010


def test_enumerate_three_stage_fully_differential_nonempty():
    """FD 3-stage topologies enumerate ~1.38M circuits (70 x 3^9, see
    test_enumerate_circuits_fully_differential_count for the 70 factor);
    just check the iterator yields a valid first circuit without
    materializing the full set."""
    modules = load_modules()
    topologies = load_topologies()
    for name in ("three_stage_opamp_nmc_fully_differential", "three_stage_opamp_rnmc_fully_differential"):
        topo = next(t for t in topologies if t.name == name)
        circuit = next(enumerate_circuits(topo, modules))
        assert circuit.topology == name
        assert circuit.external_ports == ["ibias", "vcm_ref", "in1", "in2", "outp", "outn", "vdd!", "gnd!"]


def test_synthesize_three_stage_single_ended_filters():
    """Filtering by stages=3 + output_type + compensation_scheme selects exactly
    one of the new 3-stage single-ended topologies."""
    nmc = synthesize({"stages": 3, "output_type": "single_ended", "compensation_scheme": "nested_miller"})
    rnmc = synthesize({"stages": 3, "output_type": "single_ended", "compensation_scheme": "reversed_nested_miller"})

    assert len(nmc) == 17010
    assert all(c.topology == "three_stage_opamp_nmc_single_ended" for c in nmc)

    assert len(rnmc) == 17010
    assert all(c.topology == "three_stage_opamp_rnmc_single_ended" for c in rnmc)


def test_three_stage_nmc_flat_spice_structure():
    modules = load_modules()
    topologies = load_topologies()
    topo = next(t for t in topologies if t.name == "three_stage_opamp_nmc_single_ended")

    simple_modules = {
        "input_pair": [v for v in modules["input_pair"] if v.name == "differential_pair_pmos"],
        "load": [v for v in modules["load"] if v.name == "resistor_load_gnd"],
        "tail_current": [v for v in modules["tail_current"] if v.name == "resistor_tail_vdd"],
        "bias_generation": [v for v in modules["bias_generation"] if v.name == "resistor_bias"],
        "second_stage": [v for v in modules["second_stage"] if v.name == "common_source"],
        "compensation": [v for v in modules["compensation"] if v.name == "miller_cap"],
    }

    circuit = next(enumerate_circuits(topo, simple_modules))
    spice = to_flat_spice(circuit, name="test_3stage")

    assert spice.startswith(".subckt test_3stage")
    assert spice.endswith(".ends")
    for port in topo.external_ports:
        assert port in spice.split("\n")[0]

    lines = spice.split("\n")
    assert sum(1 for l in lines if l.startswith("comp1_")) == 1
    assert sum(1 for l in lines if l.startswith("comp2_")) == 1
    assert any(l.startswith("second_stage_") for l in lines)
    assert any(l.startswith("third_stage_") for l in lines)


def test_three_stage_rnmc_hierarchical_spice():
    modules = load_modules()
    topologies = load_topologies()
    topo = next(t for t in topologies if t.name == "three_stage_opamp_rnmc_single_ended")

    simple_modules = {
        "input_pair": [v for v in modules["input_pair"] if v.name == "differential_pair_nmos"],
        "load": [v for v in modules["load"] if v.name == "active_load_pmos"],
        "tail_current": [v for v in modules["tail_current"] if v.name == "current_mirror_tail_nmos"],
        "bias_generation": [v for v in modules["bias_generation"] if v.name == "diode_connected_mosfet_bias"],
        "second_stage": [v for v in modules["second_stage"] if v.name == "common_drain"],
        "compensation": [v for v in modules["compensation"] if v.name == "miller_cap_with_nulling_resistor"],
    }

    circuit = next(enumerate_circuits(topo, simple_modules))
    spice = to_hierarchical_spice(circuit, name="test_3stage_rnmc_hier")

    assert ".subckt common_drain" in spice
    assert ".subckt miller_cap_with_nulling_resistor" in spice
    assert ".subckt test_3stage_rnmc_hier" in spice
    assert "Xsecond_stage" in spice
    assert "Xthird_stage" in spice
    assert "Xcomp1" in spice
    assert "Xcomp2" in spice


def _variant_map_for(modules, topo, overrides):
    """Build a variant_map covering every slot in *topo*: ``overrides`` picks
    a variant by name for specific slots, every other slot gets its first
    available variant (its choice doesn't affect bias-rail usage)."""
    variant_map = {}
    for slot in topo.slots:
        if slot.name in overrides:
            variant_map[slot.name] = next(
                v for v in modules[slot.category] if v.name == overrides[slot.name]
            )
        else:
            variant_map[slot.name] = modules[slot.category][0]
    return variant_map


def test_needed_bias_outputs_simple_load_one_stage():
    """A simple load (no cascode bias inputs) with a resistor tail (no bias
    rail) in a topology with no second_stage/third_stage slot needs none of
    the seven bias rails."""
    modules = load_modules()
    topo = next(t for t in load_topologies() if t.name == "one_stage_opamp")
    variant_map = _variant_map_for(
        modules, topo, {"load": "resistor_load_gnd", "tail_current": "resistor_tail_vdd"}
    )
    assert needed_bias_outputs(topo, variant_map) == set()


def test_needed_bias_outputs_telescopic_cascode_one_stage():
    """A telescopic cascode load only references bias1 (bias2/bias3/bias_cmfb
    are declared optional but unused)."""
    modules = load_modules()
    topo = next(t for t in load_topologies() if t.name == "one_stage_opamp")
    variant_map = _variant_map_for(
        modules,
        topo,
        {"load": "telescopic_cascode_load_pmos", "tail_current": "resistor_tail_vdd"},
    )
    assert needed_bias_outputs(topo, variant_map) == {1}


def test_needed_bias_outputs_folded_cascode_single_output():
    """A single-output folded-cascode load references bias1 and bias2."""
    modules = load_modules()
    topo = next(t for t in load_topologies() if t.name == "one_stage_opamp")
    variant_map = _variant_map_for(
        modules,
        topo,
        {
            "load": "folded_cascode_load_nmos_input_single_output",
            "tail_current": "resistor_tail_gnd",
        },
    )
    assert needed_bias_outputs(topo, variant_map) == {1, 2}


def test_needed_bias_outputs_folded_cascode_differential_output():
    """A differential-output folded-cascode load references all four bias
    rails (bias1, bias2, bias3, bias_cmfb)."""
    modules = load_modules()
    topo = next(t for t in load_topologies() if t.name == "one_stage_opamp")
    variant_map = _variant_map_for(
        modules,
        topo,
        {
            "load": "folded_cascode_load_nmos_input_differential_output",
            "tail_current": "resistor_tail_gnd",
        },
    )
    assert needed_bias_outputs(topo, variant_map) == {1, 2, 3, 4}


def test_needed_bias_outputs_second_stage_uses_rail_5():
    """Even with a simple load, two_stage_opamp_single_ended's second_stage
    slot taps its own dedicated rail 5 for its gate bias."""
    modules = load_modules()
    topo = next(t for t in load_topologies() if t.name == "two_stage_opamp_single_ended")
    variant_map = _variant_map_for(
        modules, topo, {"load": "resistor_load_gnd", "tail_current": "resistor_tail_vdd"}
    )
    assert needed_bias_outputs(topo, variant_map) == {5}


def test_needed_bias_outputs_tail_current_uses_rail_7():
    """A current-mirror tail_current variant references its own dedicated
    rail 7, independent of load/second_stage/third_stage rails."""
    modules = load_modules()
    topo = next(t for t in load_topologies() if t.name == "one_stage_opamp")
    variant_map = _variant_map_for(
        modules,
        topo,
        {"load": "resistor_load_gnd", "tail_current": "current_mirror_tail_pmos"},
    )
    assert needed_bias_outputs(topo, variant_map) == {7}


def test_needed_bias_outputs_third_stage_uses_rail_6():
    """In a three-stage topology, second_stage and third_stage use their own
    dedicated rails 5 and 6, independent of load/tail_current rails."""
    modules = load_modules()
    topo = next(t for t in load_topologies() if t.name == "three_stage_opamp_nmc_single_ended")
    variant_map = _variant_map_for(
        modules, topo, {"load": "resistor_load_gnd", "tail_current": "resistor_tail_vdd"}
    )
    assert needed_bias_outputs(topo, variant_map) == {5, 6}


def test_needed_bias_outputs_all_seven_rails():
    """A differential-output folded-cascode load (rails 1-4) plus a
    current-mirror tail (rail 7) plus second_stage/third_stage (rails 5, 6)
    together need all seven independent bias rails."""
    modules = load_modules()
    topo = next(t for t in load_topologies() if t.name == "three_stage_opamp_nmc_single_ended")
    variant_map = _variant_map_for(
        modules,
        topo,
        {
            "load": "folded_cascode_load_nmos_input_differential_output",
            "tail_current": "current_mirror_tail_nmos",
        },
    )
    assert needed_bias_outputs(topo, variant_map) == {1, 2, 3, 4, 5, 6, 7}


@pytest.mark.parametrize(
    "variant_name", ["diode_connected_mosfet_bias", "resistor_bias", "magic_battery_bias"]
)
@pytest.mark.parametrize(
    "needed,expected_out_ports,expected_n_devices",
    [
        (set(), [], 1),
        ({1}, ["out1"], 3),
        ({1, 2}, ["out1", "out2"], 5),
        ({1, 2, 3, 4}, ["out1", "out2", "out3", "out4"], 9),
        ({5}, ["out5"], 3),
        ({6}, ["out6"], 3),
        ({7}, ["out7"], 3),
        ({1, 5, 7}, ["out1", "out5", "out7"], 7),
        (
            set(range(1, 8)),
            ["out1", "out2", "out3", "out4", "out5", "out6", "out7"],
            15,
        ),
    ],
)
def test_prune_bias_generation_independent_legs_all_variants(
    variant_name, needed, expected_out_ports, expected_n_devices
):
    """Pruning keeps the shared reference device plus exactly the legs for
    needed rails (1 + 2*len(needed) devices); needed=={1..7} returns the
    variant unchanged (15 devices, identity)."""
    modules = load_modules()
    variant = next(v for v in modules["bias_generation"] if v.name == variant_name)

    pruned = prune_bias_generation(variant, needed)

    out_ports = [p.name for p in pruned.ports if p.name.startswith("out")]
    assert out_ports == expected_out_ports
    assert {"ibias", "vdd", "gnd"} <= {p.name for p in pruned.ports}
    assert len(pruned.devices) == expected_n_devices
    if needed == set(range(1, 8)):
        assert pruned is variant

    for dev in pruned.devices:
        refs = {t for t in dev.terminals.values() if t.startswith("out")}
        assert refs <= set(expected_out_ports)


@pytest.mark.parametrize(
    "variant_name,shared_ref",
    [
        ("magic_battery_bias", "mp1"),
        ("diode_connected_mosfet_bias", "mn1"),
        ("resistor_bias", "mp1"),
    ],
)
def test_prune_bias_generation_keeps_shared_reference_device(variant_name, shared_ref):
    """Every bias_generation variant's shared reference device (mirrors ibias
    onto an internal node, never touches out1..out4) survives pruning even
    when needed is empty."""
    modules = load_modules()
    variant = next(v for v in modules["bias_generation"] if v.name == variant_name)
    pruned = prune_bias_generation(variant, set())
    assert len(pruned.devices) == 1
    assert pruned.devices[0].ref == shared_ref


def test_enumerate_circuits_prunes_bias_generation_for_simple_load_one_stage():
    """one_stage_opamp has no second_stage slot and resistor_tail_vdd needs no
    bias rail, so a simple load (needing none of the four bias rails) prunes
    diode_connected_mosfet_bias down to just its shared reference device
    (mn1: ibias -> gnd)."""
    modules = load_modules()
    topo = next(t for t in load_topologies() if t.name == "one_stage_opamp")
    simple_modules = {
        "input_pair": [v for v in modules["input_pair"] if v.name == "differential_pair_pmos"],
        "load": [v for v in modules["load"] if v.name == "resistor_load_gnd"],
        "tail_current": [v for v in modules["tail_current"] if v.name == "resistor_tail_vdd"],
        "bias_generation": [v for v in modules["bias_generation"] if v.name == "diode_connected_mosfet_bias"],
    }

    circuit = next(enumerate_circuits(topo, simple_modules))
    bias_variant = circuit.variant_map["bias_gen"]

    assert [p.name for p in bias_variant.ports] == ["ibias", "vdd", "gnd"]
    assert len(bias_variant.devices) == 1

    bias_devices = {ref: dev for ref, dev in circuit.devices if ref.startswith("bias_gen_")}
    (dev,) = bias_devices.values()
    assert dev.ref == "bias_gen_mn1"
    assert dev.terminals["d"] == "ibias"
    assert dev.terminals["g"] == "ibias"
    assert dev.terminals["s"] == "gnd!"
    assert dev.terminals["b"] == "gnd!"


def test_enumerate_circuits_prunes_bias_generation_for_two_stage_simple_load():
    """two_stage_opamp_single_ended's second_stage taps its own dedicated rail
    5, and resistor_tail_vdd needs no bias rail, so even a simple load keeps
    exactly out5: diode_connected_mosfet_bias is pruned to its shared
    reference device (mn1) plus leg 5 (mn6, mp5)."""
    modules = load_modules()
    topo = next(t for t in load_topologies() if t.name == "two_stage_opamp_single_ended")
    simple_modules = {
        "input_pair": [v for v in modules["input_pair"] if v.name == "differential_pair_pmos"],
        "load": [v for v in modules["load"] if v.name == "resistor_load_gnd"],
        "tail_current": [v for v in modules["tail_current"] if v.name == "resistor_tail_vdd"],
        "bias_generation": [v for v in modules["bias_generation"] if v.name == "diode_connected_mosfet_bias"],
        "compensation": [v for v in modules["compensation"] if v.name == "miller_cap"],
        "second_stage": [v for v in modules["second_stage"] if v.name == "common_source"],
    }

    circuit = next(enumerate_circuits(topo, simple_modules))
    bias_variant = circuit.variant_map["bias_gen"]

    assert [p.name for p in bias_variant.ports] == ["ibias", "out5", "vdd", "gnd"]
    assert len(bias_variant.devices) == 3

    bias_devices = {ref: dev for ref, dev in circuit.devices if ref.startswith("bias_gen_")}
    assert set(bias_devices) == {"bias_gen_mn1", "bias_gen_mn6", "bias_gen_mp5"}
    assert bias_devices["bias_gen_mn6"].terminals["d"] == "net_bias5"
    assert bias_devices["bias_gen_mn6"].terminals["g"] == "ibias"
    assert bias_devices["bias_gen_mp5"].terminals["d"] == "net_bias5"
    assert bias_devices["bias_gen_mp5"].terminals["g"] == "net_bias5"
    assert bias_devices["bias_gen_mp5"].terminals["s"] == "vdd!"


def test_enumerate_circuits_tail_current_gets_dedicated_rail_7():
    """A differential-output folded-cascode load needs all four load bias
    rails (out1..out4) and is only output_cardinality-compatible with
    fully_differential topologies, which always have >=2 stages -- so
    second_stage's rail 5 is also unavoidably present. current_mirror_tail_nmos
    needs its own dedicated rail 7 -- all are present simultaneously in
    bias_generation's static 7-leg layout, with no extension needed."""
    modules = load_modules()
    topo = next(t for t in load_topologies() if t.name == "two_stage_opamp_fully_differential")
    simple_modules = {
        "input_pair": [v for v in modules["input_pair"] if v.name == "differential_pair_nmos"],
        "load": [v for v in modules["load"] if v.name == "folded_cascode_load_nmos_input_differential_output"],
        "tail_current": [v for v in modules["tail_current"] if v.name == "current_mirror_tail_nmos"],
        "bias_generation": [v for v in modules["bias_generation"] if v.name == "diode_connected_mosfet_bias"],
        "cmfb": [v for v in modules["cmfb"] if v.name == "resistive_sense_cmfb"],
        "compensation": [v for v in modules["compensation"] if v.name == "miller_cap"],
        "second_stage": [v for v in modules["second_stage"] if v.name == "common_source"],
    }

    circuit = next(enumerate_circuits(topo, simple_modules))
    bias_variant = circuit.variant_map["bias_gen"]

    assert [p.name for p in bias_variant.ports] == [
        "ibias", "out1", "out2", "out3", "out4", "out5", "out7", "vdd", "gnd",
    ]
    assert len(bias_variant.devices) == 13

    bias_devices = {ref: dev for ref, dev in circuit.devices if ref.startswith("bias_gen_")}
    assert "bias_gen_mn8" in bias_devices  # leg 7
    assert "bias_gen_mp7" in bias_devices  # leg 7
    assert bias_devices["bias_gen_mn8"].terminals["d"] == "net_bias7"
    assert bias_devices["bias_gen_mp7"].terminals["d"] == "net_bias7"
    assert bias_devices["bias_gen_mp7"].terminals["g"] == "net_bias7"
    assert bias_devices["bias_gen_mp7"].terminals["s"] == "vdd!"

    tail_devices = {ref: dev for ref, dev in circuit.devices if ref.startswith("tail_current_")}
    assert tail_devices["tail_current_m1"].terminals["d"] == "net_bias7"
    assert tail_devices["tail_current_m1"].terminals["g"] == "net_bias7"


@pytest.mark.parametrize(
    "bias_variant_name", ["diode_connected_mosfet_bias", "resistor_bias", "magic_battery_bias"]
)
def test_enumerate_circuits_tail_current_uses_rail_7_simple_load(bias_variant_name):
    """one_stage_opamp + simple load (load_needed={}) + current_mirror_tail_nmos
    (needs bias): tail gets its dedicated rail 7 (net_bias7); bias_gen is
    pruned to shared reference + leg 7 only."""
    modules = load_modules()
    topo = next(t for t in load_topologies() if t.name == "one_stage_opamp")
    simple_modules = {
        "input_pair": [v for v in modules["input_pair"] if v.name == "differential_pair_nmos"],
        "load": [v for v in modules["load"] if v.name == "active_load_pmos"],
        "tail_current": [v for v in modules["tail_current"] if v.name == "current_mirror_tail_nmos"],
        "bias_generation": [v for v in modules["bias_generation"] if v.name == bias_variant_name],
    }

    circuit = next(enumerate_circuits(topo, simple_modules))
    bias_variant = circuit.variant_map["bias_gen"]

    assert [p.name for p in bias_variant.ports if p.name.startswith("out")] == ["out7"]
    assert len(bias_variant.devices) == 3  # shared ref + 1 leg

    tail_devices = {ref: dev for ref, dev in circuit.devices if ref.startswith("tail_current_")}
    assert tail_devices["tail_current_m1"].terminals["d"] == "net_bias7"
    assert tail_devices["tail_current_m1"].terminals["g"] == "net_bias7"


@pytest.mark.parametrize(
    "bias_variant_name", ["diode_connected_mosfet_bias", "resistor_bias", "magic_battery_bias"]
)
def test_enumerate_circuits_second_stage_and_tail_current_get_distinct_rails(bias_variant_name):
    """two_stage_opamp_single_ended + simple load (load_needed={}) +
    second_stage (rail 5) + current_mirror_tail_nmos (rail 7): the two roles
    get distinct, independent bias rails."""
    modules = load_modules()
    topo = next(t for t in load_topologies() if t.name == "two_stage_opamp_single_ended")
    simple_modules = {
        "input_pair": [v for v in modules["input_pair"] if v.name == "differential_pair_nmos"],
        "load": [v for v in modules["load"] if v.name == "resistor_load_vdd"],
        "tail_current": [v for v in modules["tail_current"] if v.name == "current_mirror_tail_nmos"],
        "bias_generation": [v for v in modules["bias_generation"] if v.name == bias_variant_name],
        "compensation": [v for v in modules["compensation"] if v.name == "miller_cap"],
        "second_stage": [v for v in modules["second_stage"] if v.name == "common_source"],
    }

    circuit = next(enumerate_circuits(topo, simple_modules))
    bias_variant = circuit.variant_map["bias_gen"]

    assert [p.name for p in bias_variant.ports if p.name.startswith("out")] == ["out5", "out7"]
    assert len(bias_variant.devices) == 5  # shared ref + 2 legs

    tail_devices = {ref: dev for ref, dev in circuit.devices if ref.startswith("tail_current_")}
    assert tail_devices["tail_current_m1"].terminals["d"] == "net_bias7"

    second_stage_devices = {ref: dev for ref, dev in circuit.devices if ref.startswith("second_stage_")}
    second_stage_terms = {t for dev in second_stage_devices.values() for t in dev.terminals.values()}
    assert "net_bias5" in second_stage_terms


def test_enumerate_circuits_resistor_tail_vdd_needs_no_bias_rail():
    """resistor_tail_vdd needs no bias rail: bias_gen pruning is driven purely
    by load_needed, no extra rail consumed."""
    modules = load_modules()
    topo = next(t for t in load_topologies() if t.name == "one_stage_opamp")
    simple_modules = {
        "input_pair": [v for v in modules["input_pair"] if v.name == "differential_pair_pmos"],
        "load": [v for v in modules["load"] if v.name == "resistor_load_gnd"],
        "tail_current": [v for v in modules["tail_current"] if v.name == "resistor_tail_vdd"],
        "bias_generation": [v for v in modules["bias_generation"] if v.name == "diode_connected_mosfet_bias"],
    }

    circuit = next(enumerate_circuits(topo, simple_modules))
    bias_variant = circuit.variant_map["bias_gen"]

    assert [p.name for p in bias_variant.ports if p.name.startswith("out")] == []
    assert len(bias_variant.devices) == 1

    tail_devices = {ref: dev for ref, dev in circuit.devices if ref.startswith("tail_current_")}
    all_terms = {t for dev in tail_devices.values() for t in dev.terminals.values()}
    assert not any(t.startswith("net_bias") for t in all_terms)


def test_enumerate_circuits_resistor_tail_gnd_needs_no_bias_rail():
    """resistor_tail_gnd needs no bias rail (mirror of the vdd case above)."""
    modules = load_modules()
    topo = next(t for t in load_topologies() if t.name == "one_stage_opamp")
    simple_modules = {
        "input_pair": [v for v in modules["input_pair"] if v.name == "differential_pair_nmos"],
        "load": [v for v in modules["load"] if v.name == "resistor_load_vdd"],
        "tail_current": [v for v in modules["tail_current"] if v.name == "resistor_tail_gnd"],
        "bias_generation": [v for v in modules["bias_generation"] if v.name == "diode_connected_mosfet_bias"],
    }

    circuit = next(enumerate_circuits(topo, simple_modules))
    bias_variant = circuit.variant_map["bias_gen"]

    assert [p.name for p in bias_variant.ports if p.name.startswith("out")] == []
    assert len(bias_variant.devices) == 1

    tail_devices = {ref: dev for ref, dev in circuit.devices if ref.startswith("tail_current_")}
    all_terms = {t for dev in tail_devices.values() for t in dev.terminals.values()}
    assert not any(t.startswith("net_bias") for t in all_terms)


def test_enumerate_circuits_third_stage_uses_rail_6():
    """In three_stage_opamp_nmc_single_ended, a simple load and resistor tail
    need no bias rails, but second_stage and third_stage each tap their own
    dedicated rail (5 and 6 respectively)."""
    modules = load_modules()
    topo = next(t for t in load_topologies() if t.name == "three_stage_opamp_nmc_single_ended")
    simple_modules = {
        "input_pair": [v for v in modules["input_pair"] if v.name == "differential_pair_pmos"],
        "load": [v for v in modules["load"] if v.name == "resistor_load_gnd"],
        "tail_current": [v for v in modules["tail_current"] if v.name == "resistor_tail_vdd"],
        "bias_generation": [v for v in modules["bias_generation"] if v.name == "diode_connected_mosfet_bias"],
        "second_stage": [v for v in modules["second_stage"] if v.name == "common_source"],
        "compensation": [v for v in modules["compensation"] if v.name == "miller_cap"],
    }

    circuit = next(enumerate_circuits(topo, simple_modules))
    bias_variant = circuit.variant_map["bias_gen"]

    assert [p.name for p in bias_variant.ports if p.name.startswith("out")] == ["out5", "out6"]
    assert len(bias_variant.devices) == 5  # shared ref + 2 legs

    second_stage_devices = {ref: dev for ref, dev in circuit.devices if ref.startswith("second_stage_")}
    third_stage_devices = {ref: dev for ref, dev in circuit.devices if ref.startswith("third_stage_")}
    second_stage_terms = {t for dev in second_stage_devices.values() for t in dev.terminals.values()}
    third_stage_terms = {t for dev in third_stage_devices.values() for t in dev.terminals.values()}
    assert "net_bias5" in second_stage_terms
    assert "net_bias6" in third_stage_terms


def test_enumerate_circuits_second_stage_p_and_n_share_rail_5():
    """two_stage_opamp_fully_differential's second_stage_p and second_stage_n
    both statically wire bias to the same rail (net_bias5) -- shared via the
    topology's wiring, with no per-combination grouping logic needed.
    resistor_load_gnd has output_cardinality None (no bias_cmfb consumer), so
    is_cmfb_compatible/prune_cmfb collapse the cmfb slot to an empty
    placeholder and rail 4 (cmfb.bias) is not needed -- only rail 5
    survives."""
    modules = load_modules()
    topo = next(t for t in load_topologies() if t.name == "two_stage_opamp_fully_differential")
    simple_modules = {
        "input_pair": [v for v in modules["input_pair"] if v.name == "differential_pair_pmos"],
        "load": [v for v in modules["load"] if v.name == "resistor_load_gnd"],
        "tail_current": [v for v in modules["tail_current"] if v.name == "resistor_tail_vdd"],
        "bias_generation": [v for v in modules["bias_generation"] if v.name == "diode_connected_mosfet_bias"],
        "cmfb": [v for v in modules["cmfb"] if v.name == "resistive_sense_cmfb"],
        "second_stage": [v for v in modules["second_stage"] if v.name == "common_source"],
        "compensation": [v for v in modules["compensation"] if v.name == "miller_cap"],
    }

    circuit = next(enumerate_circuits(topo, simple_modules))
    bias_variant = circuit.variant_map["bias_gen"]

    assert [p.name for p in bias_variant.ports if p.name.startswith("out")] == ["out5"]
    assert len(bias_variant.devices) == 3  # shared ref + 1 leg (rail 5)

    cmfb_variant = circuit.variant_map["cmfb"]
    assert cmfb_variant.devices == []

    p_devices = {ref: dev for ref, dev in circuit.devices if ref.startswith("second_stage_p_")}
    n_devices = {ref: dev for ref, dev in circuit.devices if ref.startswith("second_stage_n_")}
    p_terms = {t for dev in p_devices.values() for t in dev.terminals.values()}
    n_terms = {t for dev in n_devices.values() for t in dev.terminals.values()}
    assert "net_bias5" in p_terms
    assert "net_bias5" in n_terms


def test_enumerate_circuits_all_seven_bias_rails_independent():
    """A differential-output folded-cascode load (rails 1-3, plus
    bias_cmfb -> net_cmfb_out via the cmfb slot), second_stage and
    third_stage (rails 5 and 6), a current-mirror tail (rail 7), and the cmfb
    slot itself (rail 4, via cmfb.bias) together need all seven bias rails --
    bias_gen is unpruned (15 devices), and each role's devices reference a
    distinct net_bias{N}. A differential-output folded-cascode load is only
    output_cardinality-compatible with fully_differential topologies, so this
    uses the fully-differential 3-stage NMC topology."""
    modules = load_modules()
    topo = next(t for t in load_topologies() if t.name == "three_stage_opamp_nmc_fully_differential")
    simple_modules = {
        "input_pair": [v for v in modules["input_pair"] if v.name == "differential_pair_nmos"],
        "load": [v for v in modules["load"] if v.name == "folded_cascode_load_nmos_input_differential_output"],
        "tail_current": [v for v in modules["tail_current"] if v.name == "current_mirror_tail_nmos"],
        "bias_generation": [v for v in modules["bias_generation"] if v.name == "diode_connected_mosfet_bias"],
        "cmfb": [v for v in modules["cmfb"] if v.name == "resistive_sense_cmfb"],
        "second_stage": [v for v in modules["second_stage"] if v.name == "common_source"],
        "compensation": [v for v in modules["compensation"] if v.name == "miller_cap"],
    }

    circuit = next(enumerate_circuits(topo, simple_modules))
    bias_variant = circuit.variant_map["bias_gen"]

    assert [p.name for p in bias_variant.ports if p.name.startswith("out")] == [
        "out1", "out2", "out3", "out4", "out5", "out6", "out7",
    ]
    assert len(bias_variant.devices) == 15

    load_terms = {t for ref, dev in circuit.devices if ref.startswith("load_") for t in dev.terminals.values()}
    second_stage_terms = {t for ref, dev in circuit.devices if ref.startswith("second_stage_") for t in dev.terminals.values()}
    third_stage_terms = {t for ref, dev in circuit.devices if ref.startswith("third_stage_") for t in dev.terminals.values()}
    tail_terms = {t for ref, dev in circuit.devices if ref.startswith("tail_current_") for t in dev.terminals.values()}
    cmfb_terms = {t for ref, dev in circuit.devices if ref.startswith("cmfb_") for t in dev.terminals.values()}

    assert {"net_bias1", "net_bias2", "net_bias3"} <= load_terms
    assert "net_cmfb_out" in load_terms
    assert "net_bias5" in second_stage_terms
    assert "net_bias6" in third_stage_terms
    assert "net_bias7" in tail_terms
    assert "net_bias4" in cmfb_terms
    assert "net_loadout1" in cmfb_terms
    assert "net_loadout2" in cmfb_terms
    assert "net_cmfb_out" in cmfb_terms


def test_synthesize_differential_output_folded_cascode_has_nondegenerate_cascode_devices():
    """folded_cascode_load_nmos_input_differential_output's out1/out2 are
    wired to dedicated net_loadout1/net_loadout2 nets, distinct from in1/in2
    (net_diff1/net_diff2) -- so the cascode devices whose drain is out1/out2
    have a different source net (no longer Vds=0). cmfb's sense inputs land
    on the same net_loadout1/net_loadout2 nets, confirming cmfb senses the
    load's actual cascode output rather than the folding node."""
    modules = load_modules()
    topologies = load_topologies()
    topo = next(t for t in topologies if t.name == "two_stage_opamp_fully_differential")

    simple_modules = {
        "input_pair": [v for v in modules["input_pair"] if v.name == "differential_pair_nmos"],
        "load": [v for v in modules["load"] if v.name == "folded_cascode_load_nmos_input_differential_output"],
        "tail_current": [v for v in modules["tail_current"] if v.name == "current_mirror_tail_nmos"],
        "bias_generation": [v for v in modules["bias_generation"] if v.name == "diode_connected_mosfet_bias"],
        "cmfb": [v for v in modules["cmfb"] if v.name == "resistive_sense_cmfb"],
        "compensation": [v for v in modules["compensation"] if v.name == "miller_cap"],
        "second_stage": [v for v in modules["second_stage"] if v.name == "common_source"],
    }

    circuit = next(enumerate_circuits(topo, simple_modules))
    load_devices = {ref: dev for ref, dev in circuit.devices if ref.startswith("load_")}
    cmfb_devices = {ref: dev for ref, dev in circuit.devices if ref.startswith("cmfb_")}

    cascode_outputs = {"net_loadout1", "net_loadout2"}
    cascode_devices = [dev for dev in load_devices.values() if dev.terminals.get("d") in cascode_outputs]
    assert len(cascode_devices) == 4  # mp3/mp4 (cascode) + mn1/mn2 (folded branch)
    for dev in cascode_devices:
        assert dev.terminals["d"] != dev.terminals["s"]

    cmfb_sensed = {dev.terminals["t1"] for dev in cmfb_devices.values() if "t1" in dev.terminals}
    assert cascode_outputs == cmfb_sensed


def test_synthesize_single_output_cascode_load_has_nondegenerate_output_device():
    """telescopic_cascode_load_pmos's out is wired to the stage's output net
    (out), distinct from in1/in2 (net_diff1/net_fold2) -- so the cascode
    device whose drain is out has a different source net (no longer
    Vds=0)."""
    modules = load_modules()
    topologies = load_topologies()
    topo = next(t for t in topologies if t.name == "one_stage_opamp")

    simple_modules = {
        "input_pair": [v for v in modules["input_pair"] if v.name == "differential_pair_pmos"],
        "load": [v for v in modules["load"] if v.name == "telescopic_cascode_load_pmos"],
        "tail_current": [v for v in modules["tail_current"] if v.name == "resistor_tail_vdd"],
        "bias_generation": [v for v in modules["bias_generation"] if v.name == "diode_connected_mosfet_bias"],
    }

    circuit = next(enumerate_circuits(topo, simple_modules))
    load_devices = {ref: dev for ref, dev in circuit.devices if ref.startswith("load_")}

    output_device = next(dev for dev in load_devices.values() if dev.terminals.get("d") == "out")
    assert output_device.terminals["s"] != "out"


def test_synthesize_alias_of_load_merges_in_and_out_nets():
    """resistor_load_vdd declares out1/out2 as alias_of in1/in2. The topology
    wires load.in1 and load.out1 to separate nets (net_diff1 and
    net_loadout1), but the net-merge pass collapses them back into one --
    so load_r1 (load.in1), input_pair_m1 (input_pair.out1), and
    second_stage_n_mn1 (which senses the load's output) all land on the same
    net, restoring the single shared in/out node these devices assume."""
    modules = load_modules()
    topologies = load_topologies()
    topo = next(t for t in topologies if t.name == "two_stage_opamp_fully_differential")

    simple_modules = {
        "input_pair": [v for v in modules["input_pair"] if v.name == "differential_pair_nmos"],
        "load": [v for v in modules["load"] if v.name == "resistor_load_vdd"],
        "tail_current": [v for v in modules["tail_current"] if v.name == "resistor_tail_gnd"],
        "bias_generation": [v for v in modules["bias_generation"] if v.name == "diode_connected_mosfet_bias"],
        "cmfb": [v for v in modules["cmfb"] if v.name == "resistive_sense_cmfb"],
        "compensation": [v for v in modules["compensation"] if v.name == "miller_cap"],
        "second_stage": [v for v in modules["second_stage"] if v.name == "common_source"],
    }

    circuit = next(enumerate_circuits(topo, simple_modules))
    devices = dict(circuit.devices)

    load_in1_net = devices["load_r1"].terminals["t2"]
    input_pair_out1_net = devices["input_pair_m1"].terminals["d"]
    second_stage_n_sense_net = devices["second_stage_n_mn1"].terminals["g"]

    assert load_in1_net == input_pair_out1_net == second_stage_n_sense_net
