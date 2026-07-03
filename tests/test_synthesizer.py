import pytest
from circuitgenome.synthesizer.bias_construction import (
    construct_bias_generation,
    rail_flavor_from_diode,
    required_rail_kinds,
)
from circuitgenome.synthesizer.cmfb_compatibility import CANONICAL_CMFB_VARIANT, is_cmfb_compatible, prune_cmfb
from circuitgenome.synthesizer.polarity_compatibility import is_combination_valid
from circuitgenome.synthesizer.output_compatibility import is_output_type_compatible
from circuitgenome.synthesizer.tail_current_compatibility import (
    CANONICAL_TAIL_CURRENT_VARIANT,
    is_tail_current_compatible,
    prune_tail_current,
)
from circuitgenome.synthesizer.loader import load_bias_legs, load_modules, load_topologies
from circuitgenome.synthesizer.synthesizer import enumerate_circuits, synthesize
from circuitgenome.synthesizer.netlist import to_flat_spice, to_hierarchical_spice


def test_load_modules():
    modules = load_modules()
    assert "input_pair" in modules
    assert "load" in modules
    assert "tail_current" in modules
    # bias_generation has no enumerated variants: the bias generator is
    # constructed per combination (bias_construction.py).
    assert "bias_generation" not in modules
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
        "cmfb": [v for v in modules["cmfb"] if v.name == "resistive_sense_cmfb"],
        "compensation": [v for v in modules["compensation"] if v.name == "miller_cap"],
        "second_stage": [v for v in modules["second_stage"] if v.name == "common_source"],
    }

    circuit = next(enumerate_circuits(topo, simple_modules))
    load_devices = {ref: dev for ref, dev in circuit.devices if ref.endswith("_load")}

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


def test_bias_leg_library_structure():
    """The leg library provides the multi-reference core plus exactly the
    five rail kinds, and every template's nets stay within the contract
    (ibias/pref/out/vdd/gnd).  Legs referencing pref are the ones the pref
    branch exists for; the gate_vdd and current_sink legs mirror the master
    directly via ibias."""
    library = load_bias_legs()

    assert set(library.legs) == {
        "gate_vdd", "gate_gnd", "current_source", "current_sink", "tunable",
    }
    assert len(library.reference) == 1
    (mref,) = library.reference
    assert mref.type == "nmos"
    assert mref.terminals["d"] == mref.terminals["g"] == "ibias"

    # pref branch: NMOS mirror of the master into a diode-connected PMOS.
    assert [d.type for d in library.pref_branch] == ["nmos", "pmos"]
    assert library.pref_branch[1].terminals["d"] == library.pref_branch[1].terminals["g"] == "pref"

    allowed_nets = {"ibias", "pref", "out", "vdd", "gnd"}
    for kind, devices in library.legs.items():
        for dev in devices:
            assert set(dev.terminals.values()) <= allowed_nets, (kind, dev.ref)
        assert any("out" in d.terminals.values() for d in devices), kind

    uses_pref = {
        kind
        for kind, devices in library.legs.items()
        if any("pref" in d.terminals.values() for d in devices)
    }
    assert uses_pref == {"gate_gnd", "current_source", "tunable"}

    # Current legs are bare mirrors: no diode of their own (the consumer's
    # reference diode owns the rail voltage).
    for kind in ("current_source", "current_sink"):
        (dev,) = library.legs[kind]
        assert dev.terminals["d"] == "out" and dev.terminals["g"] != "out"


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
    cmfb.bias is not consumed (required_rail_kinds)."""
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
        cmfb_device_refs = [ref for ref, _ in circuit.devices if ref.endswith("_cmfb")]
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
    tail_current.bias is not consumed (required_rail_kinds)."""
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

        tail_current_device_refs = [ref for ref, _ in circuit.devices if ref.endswith("_tail_current")]
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
    test_is_output_type_compatible_*). The bias generator is constructed,
    not enumerated, so it contributes no factor: 70 x 3 second_stage x 3
    comp = 630."""
    modules = load_modules()
    topologies = load_topologies()
    topo = next(t for t in topologies if t.name == "two_stage_opamp_single_ended")
    circuits = list(enumerate_circuits(topo, modules))
    assert len(circuits) == 630


def test_enumerate_circuits_fully_differential_count():
    """2-stage fully-differential: of the 84 effective input_pair/load/
    tail_current combinations (144 polarity-valid, collapsed to 84 by
    is_tail_current_compatible -- see test_enumerate_circuits_count), 56 have
    an output_cardinality compatible with fully_differential (the 28
    "single"-cardinality combos are excluded). Of those 56, 14 use a
    "differential"-cardinality load -- the only loads with a real bias_cmfb
    consumer -- and keep both cmfb variants (14 x 2 = 28); the other 42 have
    no bias_cmfb consumer, so is_cmfb_compatible collapses cmfb to 1 canonical
    variant (42 x 1 = 42). 28 + 42 = 70 effective load/cmfb combinations.
    The bias generator is constructed, not enumerated, so it contributes no
    factor: 70 x 9 (second_stage_p x second_stage_n) x 9 (comp_p x comp_n)
    = 5670."""
    modules = load_modules()
    topologies = load_topologies()
    topo = next(t for t in topologies if t.name == "two_stage_opamp_fully_differential")
    circuits = list(enumerate_circuits(topo, modules))
    assert len(circuits) == 5670


def test_flat_spice_structure():
    modules = load_modules()
    topologies = load_topologies()
    topo = next(t for t in topologies if t.name == "one_stage_opamp")

    # Use the simplest variants for a deterministic test
    simple_modules = {
        "input_pair": [v for v in modules["input_pair"] if v.name == "differential_pair_pmos"],
        "load": [v for v in modules["load"] if v.name == "resistor_load_gnd"],
        "tail_current": [v for v in modules["tail_current"] if v.name == "resistor_tail_vdd"],
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
    input_pair/load/tail_current combinations (see test_enumerate_circuits_count).
    The bias generator is constructed, not enumerated, so it contributes no
    factor: 70 x 9 (second_stage x third_stage) x 3 comp1 x 3 comp2 =
    5670."""
    modules = load_modules()
    topologies = load_topologies()
    for name in ("three_stage_opamp_nmc_single_ended", "three_stage_opamp_rnmc_single_ended"):
        topo = next(t for t in topologies if t.name == name)
        circuits = list(enumerate_circuits(topo, modules))
        assert len(circuits) == 5670


def test_enumerate_three_stage_fully_differential_nonempty():
    """FD 3-stage topologies enumerate ~0.46M circuits (70 x 3^4 stage
    variants x 3^4 compensation variants; see
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

    assert len(nmc) == 5670
    assert all(c.topology == "three_stage_opamp_nmc_single_ended" for c in nmc)

    assert len(rnmc) == 5670
    assert all(c.topology == "three_stage_opamp_rnmc_single_ended" for c in rnmc)


def test_three_stage_nmc_flat_spice_structure():
    modules = load_modules()
    topologies = load_topologies()
    topo = next(t for t in topologies if t.name == "three_stage_opamp_nmc_single_ended")

    simple_modules = {
        "input_pair": [v for v in modules["input_pair"] if v.name == "differential_pair_pmos"],
        "load": [v for v in modules["load"] if v.name == "resistor_load_gnd"],
        "tail_current": [v for v in modules["tail_current"] if v.name == "resistor_tail_vdd"],
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
    assert sum(1 for l in lines if l.split()[0].endswith("_comp1")) == 1
    assert sum(1 for l in lines if l.split()[0].endswith("_comp2")) == 1
    assert any(l.split()[0].endswith("_second_stage") for l in lines)
    assert any(l.split()[0].endswith("_third_stage") for l in lines)


def test_three_stage_rnmc_hierarchical_spice():
    modules = load_modules()
    topologies = load_topologies()
    topo = next(t for t in topologies if t.name == "three_stage_opamp_rnmc_single_ended")

    simple_modules = {
        "input_pair": [v for v in modules["input_pair"] if v.name == "differential_pair_nmos"],
        "load": [v for v in modules["load"] if v.name == "active_load_pmos"],
        "tail_current": [v for v in modules["tail_current"] if v.name == "current_mirror_tail_nmos"],
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
    """Build a variant_map covering every enumerated slot in *topo*:
    ``overrides`` picks a variant by name for specific slots, every other
    slot gets its first available variant (its choice doesn't affect
    bias-rail usage).  The bias_generation slot is skipped -- its variant is
    constructed from the others, not enumerated."""
    variant_map = {}
    for slot in topo.slots:
        if slot.category == "bias_generation":
            continue
        if slot.name in overrides:
            variant_map[slot.name] = next(
                v for v in modules[slot.category] if v.name == overrides[slot.name]
            )
        else:
            variant_map[slot.name] = modules[slot.category][0]
    return variant_map


# ─── Typed rail-kind demand analysis (bias_construction.py) ─────────────────


def test_required_rail_kinds_simple_load_one_stage():
    """A simple load (no bias inputs) with a resistor tail (no bias rail) in
    a topology with no second_stage/third_stage slot consumes none of the
    seven bias rails -- there is nothing to construct."""
    modules = load_modules()
    topo = next(t for t in load_topologies() if t.name == "one_stage_opamp")
    variant_map = _variant_map_for(
        modules, topo, {"load": "resistor_load_gnd", "tail_current": "resistor_tail_vdd"}
    )
    assert required_rail_kinds(topo, variant_map) == {}


def test_required_rail_kinds_telescopic_cascode_is_tunable():
    """A telescopic cascode load only references bias1
    (bias2/bias3/bias_cmfb are declared optional but unused), and its
    consumers are cascode gates whose source is an internal node -- no
    structural level is implied, so the rail falls back to the tunable
    resistor leg."""
    modules = load_modules()
    topo = next(t for t in load_topologies() if t.name == "one_stage_opamp")
    variant_map = _variant_map_for(
        modules,
        topo,
        {"load": "telescopic_cascode_load_pmos", "tail_current": "resistor_tail_vdd"},
    )
    assert required_rail_kinds(topo, variant_map) == {1: "tunable"}


def test_required_rail_kinds_folded_cascode_single_output():
    """A single-output folded-cascode load references bias1 (folding-source
    gates with source on a supply -> gate kind of that supply) and bias2
    (cascode gates, internal-node source -> tunable)."""
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
    assert required_rail_kinds(topo, variant_map) == {1: "gate_vdd", 2: "tunable"}


def test_required_rail_kinds_folded_cascode_differential_output():
    """A differential-output folded-cascode load references all four of its
    bias rails: folding sources at vdd (rail 1 gate_vdd), two cascode ranks
    (rails 2/3 tunable), and the output tail sinks at gnd (rail 4
    gate_gnd -- wired straight to net_bias4 in topologies without a cmfb
    slot)."""
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
    assert required_rail_kinds(topo, variant_map) == {
        1: "gate_vdd", 2: "tunable", 3: "tunable", 4: "gate_gnd",
    }


@pytest.mark.parametrize(
    "stage_name,expected",
    [
        ("common_source", "gate_vdd"),   # PMOS current source, source at vdd
        ("common_drain", "gate_gnd"),    # NMOS sink, source at gnd
        ("differential_ota_second_stage", "gate_vdd"),
    ],
)
def test_required_rail_kinds_second_stage_rail_5(stage_name, expected):
    """Even with a simple load, two_stage_opamp_single_ended's second_stage
    slot taps its own dedicated rail 5; the kind follows the supply its bias
    gate's source sits on."""
    modules = load_modules()
    topo = next(t for t in load_topologies() if t.name == "two_stage_opamp_single_ended")
    variant_map = _variant_map_for(
        modules,
        topo,
        {
            "load": "resistor_load_gnd",
            "tail_current": "resistor_tail_vdd",
            "second_stage": stage_name,
        },
    )
    assert required_rail_kinds(topo, variant_map) == {5: expected}


@pytest.mark.parametrize(
    "tail_name,expected",
    [
        ("current_mirror_tail_nmos", "current_source"),
        ("cascode_current_mirror_tail_nmos", "current_source"),
        ("current_mirror_tail_pmos", "current_sink"),
        ("cascode_current_mirror_tail_pmos", "current_sink"),
    ],
)
def test_required_rail_kinds_mirror_tails_are_current_interfaces(tail_name, expected):
    """A mirror tail brings its own reference diode on rail 7, making the
    rail a *current* interface: an NMOS diode wants current sourced in, a
    PMOS diode wants it sunk out. The diode vote wins over the tail's own
    mirror-output gate riding on the same rail, and the cascode tails'
    stacked diode (internal-node source) resolves by channel type."""
    modules = load_modules()
    topo = next(t for t in load_topologies() if t.name == "one_stage_opamp")
    variant_map = _variant_map_for(
        modules, topo, {"load": "resistor_load_gnd", "tail_current": tail_name}
    )
    assert required_rail_kinds(topo, variant_map) == {7: expected}


def test_required_rail_kinds_third_stage_uses_rail_6():
    """In a three-stage topology, second_stage and third_stage tap their own
    dedicated rails 5 and 6, independent of load/tail_current rails."""
    modules = load_modules()
    topo = next(t for t in load_topologies() if t.name == "three_stage_opamp_nmc_single_ended")
    variant_map = _variant_map_for(
        modules, topo, {"load": "resistor_load_gnd", "tail_current": "resistor_tail_vdd"}
    )
    assert required_rail_kinds(topo, variant_map) == {5: "gate_vdd", 6: "gate_vdd"}


@pytest.mark.parametrize("cmfb_name", ["resistive_sense_cmfb", "dda_cmfb"])
def test_required_rail_kinds_cmfb_rail_4(cmfb_name):
    """Both cmfb variants sink their tail current through an NMOS gated by
    rail 4 (source at gnd), so a real cmfb always makes rail 4 gate_gnd --
    one generator now serves it alongside the vdd-flavored rails 1/5 (the
    consumer set that used to be routed to resistor_bias only)."""
    modules = load_modules()
    topo = next(t for t in load_topologies() if t.name == "two_stage_opamp_fully_differential")
    variant_map = _variant_map_for(
        modules,
        topo,
        {
            "load": "folded_cascode_load_nmos_input_differential_output",
            "tail_current": "resistor_tail_gnd",
            "cmfb": cmfb_name,
            "second_stage_p": "common_source",
            "second_stage_n": "common_source",
        },
    )
    assert required_rail_kinds(topo, variant_map) == {
        1: "gate_vdd", 2: "tunable", 3: "tunable", 4: "gate_gnd", 5: "gate_vdd",
    }


def test_required_rail_kinds_conflicting_gate_votes_fall_to_tunable():
    """second_stage_p and second_stage_n share rail 5; picking one variant of
    each flavor makes the rail demand both supplies at once -- no diode leg
    can serve both, so the rail falls back to the tunable resistor leg."""
    modules = load_modules()
    topo = next(t for t in load_topologies() if t.name == "two_stage_opamp_fully_differential")
    variant_map = _variant_map_for(
        modules,
        topo,
        {
            "load": "resistor_load_gnd",
            "tail_current": "resistor_tail_vdd",
            "second_stage_p": "common_source",
            "second_stage_n": "common_drain",
        },
    )
    assert required_rail_kinds(topo, variant_map)[5] == "tunable"


def test_required_rail_kinds_ignores_pruned_placeholders():
    """Construction runs after prune_cmfb: a load with no real bias_cmfb
    consumer gets an empty cmfb placeholder, so rail 4 is not consumed at
    all -- whereas the raw (unpruned) cmfb variant would demand a gate_gnd
    leg for a rail that drives nothing. build_circuit guarantees this
    ordering (see the enumerate_circuits pipeline)."""
    modules = load_modules()
    topo = next(t for t in load_topologies() if t.name == "two_stage_opamp_fully_differential")
    variant_map = _variant_map_for(
        modules,
        topo,
        {
            "load": "resistor_load_gnd",
            "tail_current": "resistor_tail_vdd",
            "cmfb": CANONICAL_CMFB_VARIANT,
            "second_stage_p": "common_source",
            "second_stage_n": "common_source",
        },
    )
    assert 4 in required_rail_kinds(topo, variant_map)  # raw cmfb: wrong
    variant_map["cmfb"] = prune_cmfb(variant_map["cmfb"], variant_map["load"])
    assert required_rail_kinds(topo, variant_map) == {5: "gate_vdd"}


def test_required_rail_kinds_all_seven_rails():
    """A differential-output folded-cascode load (rails 1-4), second_stage
    and third_stage (rails 5, 6), and a current-mirror tail (rail 7)
    together consume all seven rails, each with its own kind."""
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
    assert required_rail_kinds(topo, variant_map) == {
        1: "gate_vdd", 2: "tunable", 3: "tunable", 4: "gate_gnd",
        5: "gate_vdd", 6: "gate_vdd", 7: "current_source",
    }


@pytest.mark.parametrize(
    "tail_name,expected",
    [
        ("current_mirror_tail_pmos", "vdd"),
        ("cascode_current_mirror_tail_pmos", "vdd"),
        ("current_mirror_tail_nmos", "gnd"),
        ("cascode_current_mirror_tail_nmos", "gnd"),
        ("resistor_tail_vdd", None),
        ("resistor_tail_gnd", None),
    ],
)
def test_rail_flavor_from_diode_tail_reference(tail_name, expected):
    """The mirror tails' reference diode on ``bias`` resolves by channel
    type, including the cascode tails' stacked diode whose source is an
    internal node; resistor tails have no diode."""
    modules = load_modules()
    tail = next(v for v in modules["tail_current"] if v.name == tail_name)
    assert rail_flavor_from_diode(tail.devices, "bias") == expected


# ─── Bias construction (bias_construction.py) ───────────────────────────────


def _constructed_for(topo_name, overrides):
    modules = load_modules()
    topo = next(t for t in load_topologies() if t.name == topo_name)
    variant_map = _variant_map_for(modules, topo, overrides)
    return construct_bias_generation(topo, variant_map, load_bias_legs())


def test_construct_bias_generation_no_consumed_rails():
    """No consumed rails -> just the master reference (which also gives the
    ibias pin its DC path), no pref branch, no out ports."""
    variant = _constructed_for(
        "one_stage_opamp",
        {"load": "resistor_load_gnd", "tail_current": "resistor_tail_vdd"},
    )
    assert variant.name == "constructed_bias"
    assert variant.category == "bias_generation"
    assert [p.name for p in variant.ports] == ["ibias", "vdd", "gnd"]
    assert [d.ref for d in variant.devices] == ["mnref"]


def test_construct_bias_generation_gate_vdd_leg_mirrors_master_directly():
    """A gate_vdd rail needs no pref branch: its NMOS mirror gates on ibias
    and its diode-connected PMOS is the mirror master of the consumer's
    gate."""
    variant = _constructed_for(
        "two_stage_opamp_single_ended",
        {
            "load": "resistor_load_gnd",
            "tail_current": "resistor_tail_vdd",
            "second_stage": "common_source",
        },
    )
    assert [p.name for p in variant.ports] == ["ibias", "out5", "vdd", "gnd"]
    devices = {d.ref: d for d in variant.devices}
    assert set(devices) == {"mnref", "mn5", "mp5"}
    assert devices["mn5"].terminals == {"d": "out5", "g": "ibias", "s": "gnd", "b": "gnd"}
    assert devices["mp5"].terminals == {"d": "out5", "g": "out5", "s": "vdd", "b": "vdd"}


def test_construct_bias_generation_gate_gnd_leg_brings_pref_branch():
    """A gate_gnd rail mirrors from the PMOS-side reference: the pref branch
    is emitted (once) and the leg's diode-connected NMOS is the mirror
    master of the consumer's gate. pref is not a port -- it stays a
    slot-internal net."""
    variant = _constructed_for(
        "two_stage_opamp_single_ended",
        {
            "load": "resistor_load_gnd",
            "tail_current": "resistor_tail_vdd",
            "second_stage": "common_drain",
        },
    )
    assert [p.name for p in variant.ports] == ["ibias", "out5", "vdd", "gnd"]
    devices = {d.ref: d for d in variant.devices}
    assert set(devices) == {"mnref", "mnpref", "mppref", "mp5", "mn5"}
    assert devices["mp5"].terminals == {"d": "out5", "g": "pref", "s": "vdd", "b": "vdd"}
    assert devices["mn5"].terminals == {"d": "out5", "g": "out5", "s": "gnd", "b": "gnd"}
    assert "pref" not in {p.name for p in variant.ports}


def test_construct_bias_generation_mixed_flavors_share_one_generator():
    """The redesign's flagship case: a real-cmfb fully-differential consumer
    set mixes gate_vdd (rails 1, 5), gate_gnd (rail 4), and tunable (rails
    2, 3) demands -- one constructed generator serves all of them, with a
    single shared pref branch.  Under the retired single-flavor variants
    this set could only enumerate with resistor_bias."""
    variant = _constructed_for(
        "two_stage_opamp_fully_differential",
        {
            "load": "folded_cascode_load_nmos_input_differential_output",
            "tail_current": "resistor_tail_gnd",
            "cmfb": "resistive_sense_cmfb",
            "second_stage_p": "common_source",
            "second_stage_n": "common_source",
        },
    )
    assert [p.name for p in variant.ports] == [
        "ibias", "out1", "out2", "out3", "out4", "out5", "vdd", "gnd",
    ]
    devices = {d.ref: d for d in variant.devices}
    # master + one pref branch + 5 legs of 2 devices
    assert len(devices) == 13
    assert {"mnpref", "mppref"} <= set(devices)
    # gate_vdd legs (rails 1, 5): PMOS diode on the rail
    for i in (1, 5):
        assert devices[f"mp{i}"].terminals["g"] == f"out{i}"
    # gate_gnd leg (rail 4): NMOS diode on the rail, PMOS mirror from pref
    assert devices["mn4"].terminals["g"] == "out4"
    assert devices["mp4"].terminals["g"] == "pref"
    # tunable legs (rails 2, 3): resistor to gnd
    for i in (2, 3):
        assert devices[f"r{i}"].terminals == {"t1": f"out{i}", "t2": "gnd"}


@pytest.mark.parametrize(
    "tail_name,leg_refs",
    [
        # NMOS tail diode: current sourced in from a bare PMOS mirror (pref).
        ("current_mirror_tail_nmos", {"mnref", "mnpref", "mppref", "mp7"}),
        ("cascode_current_mirror_tail_nmos", {"mnref", "mnpref", "mppref", "mp7"}),
        # PMOS tail diode: current sunk out by a bare NMOS mirror (ibias).
        ("current_mirror_tail_pmos", {"mnref", "mn7"}),
        ("cascode_current_mirror_tail_pmos", {"mnref", "mn7"}),
    ],
)
def test_construct_bias_generation_rail_7_is_bare_current_leg(tail_name, leg_refs):
    """Rail 7 legs carry current into/out of the tail's own reference diode
    and bring no diode of their own -- a second diode would either split the
    reference current (same flavor) or fight the tail's diode for the rail
    voltage (cross flavor, the measured 22x contention of issue #99). Both
    failure modes are unconstructable now."""
    variant = _constructed_for(
        "one_stage_opamp",
        {"load": "resistor_load_gnd", "tail_current": tail_name},
    )
    assert [p.name for p in variant.ports if p.name.startswith("out")] == ["out7"]
    assert {d.ref for d in variant.devices} == leg_refs
    assert not any(
        d.terminals.get("d") == "out7" and d.terminals.get("g") == "out7"
        for d in variant.devices
    )



def test_enumerate_circuits_constructed_bias_matches_consumer_flavors():
    """Every synthesized 2-stage single-ended circuit gets one constructed
    generator, and its rail-5 leg tracks the second stage's demand: a PMOS
    diode (vdd-referenced mirror master) for gate-at-vdd stages, an NMOS
    diode for gate-at-gnd stages. The structurally unbiasable flavor
    mismatches issue #99 had to prune can no longer be expressed."""
    modules = load_modules()
    topo = next(t for t in load_topologies() if t.name == "two_stage_opamp_single_ended")

    rail5_diode = {"common_source": "pmos", "common_drain": "nmos",
                   "differential_ota_second_stage": "pmos"}
    for circuit in enumerate_circuits(topo, modules):
        bias = circuit.variant_map["bias_gen"]
        assert bias.name == "constructed_bias"
        expected = rail5_diode[circuit.variant_map["second_stage"].name]
        diode = next(
            d for d in bias.devices
            if d.terminals.get("d") == "out5" and d.terminals.get("g") == "out5"
        )
        assert diode.type == expected, circuit.name


def test_enumerate_circuits_constructs_bare_reference_for_simple_load_one_stage():
    """one_stage_opamp has no second_stage slot and resistor_tail_vdd needs no
    bias rail, so a simple load consumes no rails: the constructed generator
    is just the master reference (mnref: ibias -> gnd), keeping the ibias
    pin's DC path."""
    modules = load_modules()
    topo = next(t for t in load_topologies() if t.name == "one_stage_opamp")
    simple_modules = {
        "input_pair": [v for v in modules["input_pair"] if v.name == "differential_pair_pmos"],
        "load": [v for v in modules["load"] if v.name == "resistor_load_gnd"],
        "tail_current": [v for v in modules["tail_current"] if v.name == "resistor_tail_vdd"],
    }

    circuit = next(enumerate_circuits(topo, simple_modules))
    bias_variant = circuit.variant_map["bias_gen"]

    assert [p.name for p in bias_variant.ports] == ["ibias", "vdd", "gnd"]
    assert len(bias_variant.devices) == 1

    bias_devices = {ref: dev for ref, dev in circuit.devices if ref.endswith("_bias_gen")}
    (dev,) = bias_devices.values()
    assert dev.ref == "mnref_bias_gen"
    assert dev.terminals["d"] == "ibias"
    assert dev.terminals["g"] == "ibias"
    assert dev.terminals["s"] == "gnd!"
    assert dev.terminals["b"] == "gnd!"


def test_enumerate_circuits_constructs_rail_5_leg_for_two_stage_simple_load():
    """two_stage_opamp_single_ended's second_stage taps its own dedicated rail
    5, and resistor_tail_vdd needs no bias rail, so even a simple load gets
    exactly one leg: the master reference (mnref) plus the rail-5 gate_vdd
    leg (mn5, mp5) for common_source's PMOS current-source gate."""
    modules = load_modules()
    topo = next(t for t in load_topologies() if t.name == "two_stage_opamp_single_ended")
    simple_modules = {
        "input_pair": [v for v in modules["input_pair"] if v.name == "differential_pair_pmos"],
        "load": [v for v in modules["load"] if v.name == "resistor_load_gnd"],
        "tail_current": [v for v in modules["tail_current"] if v.name == "resistor_tail_vdd"],
        "compensation": [v for v in modules["compensation"] if v.name == "miller_cap"],
        "second_stage": [v for v in modules["second_stage"] if v.name == "common_source"],
    }

    circuit = next(enumerate_circuits(topo, simple_modules))
    bias_variant = circuit.variant_map["bias_gen"]

    assert [p.name for p in bias_variant.ports] == ["ibias", "out5", "vdd", "gnd"]
    assert len(bias_variant.devices) == 3

    bias_devices = {ref: dev for ref, dev in circuit.devices if ref.endswith("_bias_gen")}
    assert set(bias_devices) == {"mnref_bias_gen", "mn5_bias_gen", "mp5_bias_gen"}
    assert bias_devices["mn5_bias_gen"].terminals["d"] == "net_bias5"
    assert bias_devices["mn5_bias_gen"].terminals["g"] == "ibias"
    assert bias_devices["mp5_bias_gen"].terminals["d"] == "net_bias5"
    assert bias_devices["mp5_bias_gen"].terminals["g"] == "net_bias5"
    assert bias_devices["mp5_bias_gen"].terminals["s"] == "vdd!"


def test_enumerate_circuits_fd_mixed_flavor_bias_in_one_generator():
    """A real-cmfb fully-differential combination mixes rail kinds --
    gate_vdd (rails 1, 5), tunable (rails 2, 3), gate_gnd (rail 4, the cmfb
    tail), and current_source (rail 7, the NMOS mirror tail's diode) -- and
    the constructed generator serves all of them at once, with a single
    shared pref branch and no rail-7 diode of its own. Under the retired
    single-flavor variants this consumer set could only enumerate with
    resistor_bias (and #100's mis-sized rails)."""
    modules = load_modules()
    topo = next(t for t in load_topologies() if t.name == "two_stage_opamp_fully_differential")
    simple_modules = {
        "input_pair": [v for v in modules["input_pair"] if v.name == "differential_pair_nmos"],
        "load": [v for v in modules["load"] if v.name == "folded_cascode_load_nmos_input_differential_output"],
        "tail_current": [v for v in modules["tail_current"] if v.name == "current_mirror_tail_nmos"],
        "cmfb": [v for v in modules["cmfb"] if v.name == "resistive_sense_cmfb"],
        "compensation": [v for v in modules["compensation"] if v.name == "miller_cap"],
        "second_stage": [v for v in modules["second_stage"] if v.name == "common_source"],
    }

    circuit = next(enumerate_circuits(topo, simple_modules))
    bias_variant = circuit.variant_map["bias_gen"]

    assert [p.name for p in bias_variant.ports] == [
        "ibias", "out1", "out2", "out3", "out4", "out5", "out7", "vdd", "gnd",
    ]
    # master + pref branch + 5 two-device legs + 1 bare current leg
    assert len(bias_variant.devices) == 14

    bias_devices = {ref: dev for ref, dev in circuit.devices if ref.endswith("_bias_gen")}
    # rail 7: bare PMOS current source from pref into the tail's own diode
    assert bias_devices["mp7_bias_gen"].terminals["d"] == "net_bias7"
    assert bias_devices["mp7_bias_gen"].terminals["g"] == "bias_gen_pref"
    assert "r7_bias_gen" not in bias_devices
    # rail 4 (cmfb tail): gnd-referenced diode leg
    assert bias_devices["mn4_bias_gen"].terminals["g"] == "net_bias4"
    # rails 2/3 (cascode gates): tunable resistor legs
    assert bias_devices["r2_bias_gen"].terminals["t2"] == "gnd!"
    assert bias_devices["r3_bias_gen"].terminals["t2"] == "gnd!"

    tail_devices = {ref: dev for ref, dev in circuit.devices if ref.endswith("_tail_current")}
    assert tail_devices["m1_tail_current"].terminals["d"] == "net_bias7"
    assert tail_devices["m1_tail_current"].terminals["g"] == "net_bias7"


@pytest.mark.parametrize(
    "tail_variant_name,expected_bias_refs",
    [
        # NMOS tail diode -> bare PMOS current-source leg (needs pref branch)
        ("current_mirror_tail_nmos",
         {"mnref_bias_gen", "mnpref_bias_gen", "mppref_bias_gen", "mp7_bias_gen"}),
        ("cascode_current_mirror_tail_nmos",
         {"mnref_bias_gen", "mnpref_bias_gen", "mppref_bias_gen", "mp7_bias_gen"}),
        # PMOS tail diode -> bare NMOS current-sink leg (master only)
        ("current_mirror_tail_pmos", {"mnref_bias_gen", "mn7_bias_gen"}),
        ("cascode_current_mirror_tail_pmos", {"mnref_bias_gen", "mn7_bias_gen"}),
    ],
)
def test_enumerate_circuits_rail_7_current_leg_feeds_tail_diode(
    tail_variant_name, expected_bias_refs
):
    """Every mirror tail gets a bare current leg on its dedicated rail 7 --
    no bias-side diode to duplicate the tail's reference (the parallel-diode
    current split) or to fight it (issue #99's cross-flavor 22x contention;
    those combinations used to be rejected outright, now the failure mode is
    unconstructable)."""
    modules = load_modules()
    topo = next(t for t in load_topologies() if t.name == "one_stage_opamp")
    polarity_nmos = tail_variant_name.endswith("_nmos")
    input_pair = "differential_pair_nmos" if polarity_nmos else "differential_pair_pmos"
    load = "active_load_pmos" if polarity_nmos else "active_load_nmos"
    simple_modules = {
        "input_pair": [v for v in modules["input_pair"] if v.name == input_pair],
        "load": [v for v in modules["load"] if v.name == load],
        "tail_current": [v for v in modules["tail_current"] if v.name == tail_variant_name],
    }

    circuit = next(enumerate_circuits(topo, simple_modules))
    bias_variant = circuit.variant_map["bias_gen"]
    assert [p.name for p in bias_variant.ports if p.name.startswith("out")] == ["out7"]
    assert not any(
        d.terminals.get("d") == "out7" and d.terminals.get("g") == "out7"
        for d in bias_variant.devices
    )

    bias_devices = {ref: dev for ref, dev in circuit.devices if ref.endswith("_bias_gen")}
    assert set(bias_devices) == expected_bias_refs

    # the tail's own reference diode is the only diode on the rail
    tail_devices = {ref: dev for ref, dev in circuit.devices if ref.endswith("_tail_current")}
    assert any(
        dev.terminals.get("d") == "net_bias7" and dev.terminals.get("g") == "net_bias7"
        for dev in tail_devices.values()
    )


def test_enumerate_circuits_second_stage_and_tail_current_get_distinct_rails():
    """two_stage_opamp_single_ended + simple load + second_stage (rail 5) +
    current_mirror_tail_nmos (rail 7): the two roles get distinct,
    independent bias rails -- and *each* gets its structurally right leg
    (vdd-referenced diode for common_source, bare current source for the
    tail), where the retired single-flavor generators could only cover this
    mixed set with resistor_bias."""
    modules = load_modules()
    topo = next(t for t in load_topologies() if t.name == "two_stage_opamp_single_ended")
    simple_modules = {
        "input_pair": [v for v in modules["input_pair"] if v.name == "differential_pair_nmos"],
        "load": [v for v in modules["load"] if v.name == "resistor_load_vdd"],
        "tail_current": [v for v in modules["tail_current"] if v.name == "current_mirror_tail_nmos"],
        "compensation": [v for v in modules["compensation"] if v.name == "miller_cap"],
        "second_stage": [v for v in modules["second_stage"] if v.name == "common_source"],
    }

    circuits = list(enumerate_circuits(topo, simple_modules))
    assert len(circuits) == 1
    circuit = circuits[0]
    bias_variant = circuit.variant_map["bias_gen"]

    assert [p.name for p in bias_variant.ports if p.name.startswith("out")] == ["out5", "out7"]
    # master + pref branch + gate_vdd leg (2) + bare current-source leg (1)
    assert len(bias_variant.devices) == 6

    tail_devices = {ref: dev for ref, dev in circuit.devices if ref.endswith("_tail_current")}
    assert tail_devices["m1_tail_current"].terminals["d"] == "net_bias7"

    second_stage_devices = {ref: dev for ref, dev in circuit.devices if ref.endswith("_second_stage")}
    second_stage_terms = {t for dev in second_stage_devices.values() for t in dev.terminals.values()}
    assert "net_bias5" in second_stage_terms


def test_enumerate_circuits_resistor_tail_vdd_needs_no_bias_rail():
    """resistor_tail_vdd needs no bias rail: bias construction is driven
    purely by the load's demands, no extra rail consumed."""
    modules = load_modules()
    topo = next(t for t in load_topologies() if t.name == "one_stage_opamp")
    simple_modules = {
        "input_pair": [v for v in modules["input_pair"] if v.name == "differential_pair_pmos"],
        "load": [v for v in modules["load"] if v.name == "resistor_load_gnd"],
        "tail_current": [v for v in modules["tail_current"] if v.name == "resistor_tail_vdd"],
    }

    circuit = next(enumerate_circuits(topo, simple_modules))
    bias_variant = circuit.variant_map["bias_gen"]

    assert [p.name for p in bias_variant.ports if p.name.startswith("out")] == []
    assert len(bias_variant.devices) == 1

    tail_devices = {ref: dev for ref, dev in circuit.devices if ref.endswith("_tail_current")}
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
    }

    circuit = next(enumerate_circuits(topo, simple_modules))
    bias_variant = circuit.variant_map["bias_gen"]

    assert [p.name for p in bias_variant.ports if p.name.startswith("out")] == []
    assert len(bias_variant.devices) == 1

    tail_devices = {ref: dev for ref, dev in circuit.devices if ref.endswith("_tail_current")}
    all_terms = {t for dev in tail_devices.values() for t in dev.terminals.values()}
    assert not any(t.startswith("net_bias") for t in all_terms)


def test_enumerate_circuits_third_stage_uses_rail_6():
    """In three_stage_opamp_nmc_single_ended, a simple load and resistor tail
    need no bias rails, but second_stage and third_stage each tap their own
    dedicated rail (5 and 6 respectively) -- two gate_vdd legs off the
    master, no pref branch."""
    modules = load_modules()
    topo = next(t for t in load_topologies() if t.name == "three_stage_opamp_nmc_single_ended")
    simple_modules = {
        "input_pair": [v for v in modules["input_pair"] if v.name == "differential_pair_pmos"],
        "load": [v for v in modules["load"] if v.name == "resistor_load_gnd"],
        "tail_current": [v for v in modules["tail_current"] if v.name == "resistor_tail_vdd"],
        "second_stage": [v for v in modules["second_stage"] if v.name == "common_source"],
        "compensation": [v for v in modules["compensation"] if v.name == "miller_cap"],
    }

    circuit = next(enumerate_circuits(topo, simple_modules))
    bias_variant = circuit.variant_map["bias_gen"]

    assert [p.name for p in bias_variant.ports if p.name.startswith("out")] == ["out5", "out6"]
    assert len(bias_variant.devices) == 5  # master + 2 gate_vdd legs

    second_stage_devices = {ref: dev for ref, dev in circuit.devices if ref.endswith("_second_stage")}
    third_stage_devices = {ref: dev for ref, dev in circuit.devices if ref.endswith("_third_stage")}
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
    placeholder and rail 4 (cmfb.bias) is not consumed -- only rail 5 gets a
    leg."""
    modules = load_modules()
    topo = next(t for t in load_topologies() if t.name == "two_stage_opamp_fully_differential")
    simple_modules = {
        "input_pair": [v for v in modules["input_pair"] if v.name == "differential_pair_pmos"],
        "load": [v for v in modules["load"] if v.name == "resistor_load_gnd"],
        "tail_current": [v for v in modules["tail_current"] if v.name == "resistor_tail_vdd"],
        "cmfb": [v for v in modules["cmfb"] if v.name == "resistive_sense_cmfb"],
        "second_stage": [v for v in modules["second_stage"] if v.name == "common_source"],
        "compensation": [v for v in modules["compensation"] if v.name == "miller_cap"],
    }

    circuit = next(enumerate_circuits(topo, simple_modules))
    bias_variant = circuit.variant_map["bias_gen"]

    assert [p.name for p in bias_variant.ports if p.name.startswith("out")] == ["out5"]
    assert len(bias_variant.devices) == 3  # master + 1 gate_vdd leg (rail 5)

    cmfb_variant = circuit.variant_map["cmfb"]
    assert cmfb_variant.devices == []

    p_devices = {ref: dev for ref, dev in circuit.devices if ref.endswith("_second_stage_p")}
    n_devices = {ref: dev for ref, dev in circuit.devices if ref.endswith("_second_stage_n")}
    p_terms = {t for dev in p_devices.values() for t in dev.terminals.values()}
    n_terms = {t for dev in n_devices.values() for t in dev.terminals.values()}
    assert "net_bias5" in p_terms
    assert "net_bias5" in n_terms


def test_enumerate_circuits_all_seven_bias_rails_independent():
    """A differential-output folded-cascode load (rails 1-3, plus
    bias_cmfb -> net_cmfb_out via the cmfb slot), second_stage and
    third_stage (rails 5 and 6), a current-mirror tail (rail 7), and the cmfb
    slot itself (rail 4, via cmfb.bias) together consume all seven bias
    rails -- the constructed generator carries one leg per rail (master +
    pref branch + five 2-device legs + two tunable legs + one bare current
    leg = 16 devices), and each role's devices reference a distinct
    net_bias{N}. A differential-output folded-cascode load is only
    output_cardinality-compatible with fully_differential topologies, so this
    uses the fully-differential 3-stage NMC topology."""
    modules = load_modules()
    topo = next(t for t in load_topologies() if t.name == "three_stage_opamp_nmc_fully_differential")
    simple_modules = {
        "input_pair": [v for v in modules["input_pair"] if v.name == "differential_pair_nmos"],
        "load": [v for v in modules["load"] if v.name == "folded_cascode_load_nmos_input_differential_output"],
        "tail_current": [v for v in modules["tail_current"] if v.name == "current_mirror_tail_nmos"],
        "cmfb": [v for v in modules["cmfb"] if v.name == "resistive_sense_cmfb"],
        "second_stage": [v for v in modules["second_stage"] if v.name == "common_source"],
        "compensation": [v for v in modules["compensation"] if v.name == "miller_cap"],
    }

    circuit = next(enumerate_circuits(topo, simple_modules))
    bias_variant = circuit.variant_map["bias_gen"]

    assert [p.name for p in bias_variant.ports if p.name.startswith("out")] == [
        "out1", "out2", "out3", "out4", "out5", "out6", "out7",
    ]
    assert len(bias_variant.devices) == 16

    load_terms = {t for ref, dev in circuit.devices if ref.endswith("_load") for t in dev.terminals.values()}
    second_stage_terms = {t for ref, dev in circuit.devices if "_second_stage" in ref for t in dev.terminals.values()}
    third_stage_terms = {t for ref, dev in circuit.devices if "_third_stage" in ref for t in dev.terminals.values()}
    tail_terms = {t for ref, dev in circuit.devices if ref.endswith("_tail_current") for t in dev.terminals.values()}
    cmfb_terms = {t for ref, dev in circuit.devices if ref.endswith("_cmfb") for t in dev.terminals.values()}

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
        "cmfb": [v for v in modules["cmfb"] if v.name == "resistive_sense_cmfb"],
        "compensation": [v for v in modules["compensation"] if v.name == "miller_cap"],
        "second_stage": [v for v in modules["second_stage"] if v.name == "common_source"],
    }

    circuit = next(enumerate_circuits(topo, simple_modules))
    load_devices = {ref: dev for ref, dev in circuit.devices if ref.endswith("_load")}
    cmfb_devices = {ref: dev for ref, dev in circuit.devices if ref.endswith("_cmfb")}

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
    }

    circuit = next(enumerate_circuits(topo, simple_modules))
    load_devices = {ref: dev for ref, dev in circuit.devices if ref.endswith("_load")}

    output_device = next(dev for dev in load_devices.values() if dev.terminals.get("d") == "out")
    assert output_device.terminals["s"] != "out"


def test_synthesize_alias_of_load_merges_in_and_out_nets():
    """resistor_load_vdd declares out1/out2 as alias_of in1/in2. The topology
    wires load.in1 and load.out1 to separate nets (net_diff1 and
    net_loadout1), but the net-merge pass collapses them back into one --
    so r1_load (load.in1), m1_input_pair (input_pair.out1), and
    mn1_second_stage_n (which senses the load's output) all land on the same
    net, restoring the single shared in/out node these devices assume."""
    modules = load_modules()
    topologies = load_topologies()
    topo = next(t for t in topologies if t.name == "two_stage_opamp_fully_differential")

    simple_modules = {
        "input_pair": [v for v in modules["input_pair"] if v.name == "differential_pair_nmos"],
        "load": [v for v in modules["load"] if v.name == "resistor_load_vdd"],
        "tail_current": [v for v in modules["tail_current"] if v.name == "resistor_tail_gnd"],
        "cmfb": [v for v in modules["cmfb"] if v.name == "resistive_sense_cmfb"],
        "compensation": [v for v in modules["compensation"] if v.name == "miller_cap"],
        "second_stage": [v for v in modules["second_stage"] if v.name == "common_source"],
    }

    circuit = next(enumerate_circuits(topo, simple_modules))
    devices = dict(circuit.devices)

    load_in1_net = devices["r1_load"].terminals["t2"]
    input_pair_out1_net = devices["m1_input_pair"].terminals["d"]
    second_stage_n_sense_net = devices["mn1_second_stage_n"].terminals["g"]

    assert load_in1_net == input_pair_out1_net == second_stage_n_sense_net
