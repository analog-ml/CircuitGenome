import pytest
from circuitgenome.synthesizer.bias_pruning import (
    assign_tail_bias_rail,
    extend_bias_generation,
    needed_bias_outputs,
    prune_bias_generation,
    tail_current_needs_bias,
)
from circuitgenome.synthesizer.compatibility import is_combination_valid
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
    """bias1/bias2/bias3/bias_cmfb of a differential-output folded-cascode
    load each resolve to a distinct net_bias rail (no floating gates, no
    accidental rail sharing)."""
    modules = load_modules()
    topologies = load_topologies()
    topo = next(t for t in topologies if t.name == "two_stage_opamp_fully_differential")

    simple_modules = {
        "input_pair": [v for v in modules["input_pair"] if v.name == "differential_pair_nmos"],
        "load": [v for v in modules["load"] if v.name == "folded_cascode_load_nmos_input_differential_output"],
        "tail_current": [v for v in modules["tail_current"] if v.name == "current_mirror_tail_nmos"],
        "bias_generation": [v for v in modules["bias_generation"] if v.name == "diode_connected_mosfet_bias"],
        "compensation": [v for v in modules["compensation"] if v.name == "miller_cap"],
        "second_stage": [v for v in modules["second_stage"] if v.name == "common_source"],
    }

    circuit = next(enumerate_circuits(topo, simple_modules))
    load_devices = {ref: dev for ref, dev in circuit.devices if ref.startswith("load_")}

    assert len(load_devices) == 8
    bias_gates = {dev.terminals["g"] for dev in load_devices.values()}
    assert bias_gates == {"net_bias1", "net_bias2", "net_bias3", "net_bias4"}


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


def test_bias_generation_variants_share_uniform_leg_structure():
    """All three bias_generation variants declare ibias/out1-4/vdd/gnd ports
    and 9 devices: 1 shared reference device (terminals never reference
    out1..out4) plus 4 legs of 2 devices each (both referencing the same
    outN)."""
    modules = load_modules()
    for variant in modules["bias_generation"]:
        port_names = [p.name for p in variant.ports]
        assert port_names == ["ibias", "out1", "out2", "out3", "out4", "vdd", "gnd"], variant.name
        assert len(variant.devices) == 9, variant.name

        shared = [
            dev for dev in variant.devices
            if not any(t.startswith("out") for t in dev.terminals.values())
        ]
        assert len(shared) == 1, variant.name

        for i in range(1, 5):
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
    input_pair/load/tail_current combinations, only 144 are polarity-valid
    (see test_polarity_filter_*) x 3 bias x 3 comp x 3 second = 3888."""
    modules = load_modules()
    topologies = load_topologies()
    topo = next(t for t in topologies if t.name == "two_stage_opamp_single_ended")
    circuits = list(enumerate_circuits(topo, modules))
    assert len(circuits) == 3888


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
    """3-stage single-ended (NMC/RNMC): 144 polarity-valid input_pair/load/tail_current
    combinations (see test_enumerate_circuits_count) x 3 bias x 3 second stages
    x 3 third stages x 3 comp1 x 3 comp2 = 34992."""
    modules = load_modules()
    topologies = load_topologies()
    for name in ("three_stage_opamp_nmc_single_ended", "three_stage_opamp_rnmc_single_ended"):
        topo = next(t for t in topologies if t.name == name)
        circuits = list(enumerate_circuits(topo, modules))
        assert len(circuits) == 34992


def test_enumerate_three_stage_fully_differential_nonempty():
    """FD 3-stage topologies enumerate ~7.1M circuits (5x12x6x3x3 x 3^8); just
    check the iterator yields a valid first circuit without materializing
    the full set."""
    modules = load_modules()
    topologies = load_topologies()
    for name in ("three_stage_opamp_nmc_fully_differential", "three_stage_opamp_rnmc_fully_differential"):
        topo = next(t for t in topologies if t.name == name)
        circuit = next(enumerate_circuits(topo, modules))
        assert circuit.topology == name
        assert circuit.external_ports == ["ibias", "in1", "in2", "outp", "outn", "vdd!", "gnd!"]


def test_synthesize_three_stage_single_ended_filters():
    """Filtering by stages=3 + output_type + compensation_scheme selects exactly
    one of the new 3-stage single-ended topologies."""
    nmc = synthesize({"stages": 3, "output_type": "single_ended", "compensation_scheme": "nested_miller"})
    rnmc = synthesize({"stages": 3, "output_type": "single_ended", "compensation_scheme": "reversed_nested_miller"})

    assert len(nmc) == 34992
    assert all(c.topology == "three_stage_opamp_nmc_single_ended" for c in nmc)

    assert len(rnmc) == 34992
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
    """A simple load (no cascode bias inputs) in a topology with no
    second_stage slot needs none of the four bias rails."""
    modules = load_modules()
    topo = next(t for t in load_topologies() if t.name == "one_stage_opamp")
    variant_map = _variant_map_for(modules, topo, {"load": "resistor_load_gnd"})
    assert needed_bias_outputs(topo, variant_map) == set()


def test_needed_bias_outputs_telescopic_cascode_one_stage():
    """A telescopic cascode load only references bias1 (bias2/bias3/bias_cmfb
    are declared optional but unused)."""
    modules = load_modules()
    topo = next(t for t in load_topologies() if t.name == "one_stage_opamp")
    variant_map = _variant_map_for(modules, topo, {"load": "telescopic_cascode_load_pmos"})
    assert needed_bias_outputs(topo, variant_map) == {1}


def test_needed_bias_outputs_folded_cascode_single_output():
    """A single-output folded-cascode load references bias1 and bias2."""
    modules = load_modules()
    topo = next(t for t in load_topologies() if t.name == "one_stage_opamp")
    variant_map = _variant_map_for(
        modules, topo, {"load": "folded_cascode_load_nmos_input_single_output"}
    )
    assert needed_bias_outputs(topo, variant_map) == {1, 2}


def test_needed_bias_outputs_folded_cascode_differential_output():
    """A differential-output folded-cascode load references all four bias
    rails (bias1, bias2, bias3, bias_cmfb)."""
    modules = load_modules()
    topo = next(t for t in load_topologies() if t.name == "one_stage_opamp")
    variant_map = _variant_map_for(
        modules, topo, {"load": "folded_cascode_load_nmos_input_differential_output"}
    )
    assert needed_bias_outputs(topo, variant_map) == {1, 2, 3, 4}


def test_needed_bias_outputs_second_stage_forces_out1():
    """Even with a simple load, two_stage_opamp_single_ended's second_stage
    slot taps out1 for its own gate bias, so out1 is always needed."""
    modules = load_modules()
    topo = next(t for t in load_topologies() if t.name == "two_stage_opamp_single_ended")
    variant_map = _variant_map_for(modules, topo, {"load": "resistor_load_gnd"})
    assert needed_bias_outputs(topo, variant_map) == {1}


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
    ],
)
def test_prune_bias_generation_independent_legs_all_variants(
    variant_name, needed, expected_out_ports, expected_n_devices
):
    """Pruning keeps the shared reference device plus exactly the legs for
    needed rails (1 + 2*len(needed) devices); needed={1,2,3,4} returns the
    variant unchanged (9 devices, identity)."""
    modules = load_modules()
    variant = next(v for v in modules["bias_generation"] if v.name == variant_name)

    pruned = prune_bias_generation(variant, needed)

    out_ports = [p.name for p in pruned.ports if p.name.startswith("out")]
    assert out_ports == expected_out_ports
    assert {"ibias", "vdd", "gnd"} <= {p.name for p in pruned.ports}
    assert len(pruned.devices) == expected_n_devices
    if needed == {1, 2, 3, 4}:
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
    """two_stage_opamp_single_ended's second_stage taps out1, and
    resistor_tail_vdd needs no bias rail, so even a simple load keeps exactly
    out1: diode_connected_mosfet_bias is pruned to its shared reference device
    (mn1) plus leg 1 (mn2, mp1)."""
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

    assert [p.name for p in bias_variant.ports] == ["ibias", "out1", "vdd", "gnd"]
    assert len(bias_variant.devices) == 3

    bias_devices = {ref: dev for ref, dev in circuit.devices if ref.startswith("bias_gen_")}
    assert set(bias_devices) == {"bias_gen_mn1", "bias_gen_mn2", "bias_gen_mp1"}
    assert bias_devices["bias_gen_mn2"].terminals["d"] == "net_bias1"
    assert bias_devices["bias_gen_mn2"].terminals["g"] == "ibias"
    assert bias_devices["bias_gen_mp1"].terminals["d"] == "net_bias1"
    assert bias_devices["bias_gen_mp1"].terminals["g"] == "net_bias1"
    assert bias_devices["bias_gen_mp1"].terminals["s"] == "vdd!"


def test_enumerate_circuits_extends_bias_generation_for_tail_bias_rail_overflow():
    """A differential-output folded-cascode load needs all four bias rails
    (out1..out4), and current_mirror_tail_nmos needs its own dedicated bias
    rail -- since out1..out4 are taken, diode_connected_mosfet_bias is
    extended with a fifth leg (out5/net_bias5) for the tail's bias."""
    modules = load_modules()
    topo = next(t for t in load_topologies() if t.name == "one_stage_opamp")
    simple_modules = {
        "input_pair": [v for v in modules["input_pair"] if v.name == "differential_pair_nmos"],
        "load": [v for v in modules["load"] if v.name == "folded_cascode_load_nmos_input_differential_output"],
        "tail_current": [v for v in modules["tail_current"] if v.name == "current_mirror_tail_nmos"],
        "bias_generation": [v for v in modules["bias_generation"] if v.name == "diode_connected_mosfet_bias"],
    }

    circuit = next(enumerate_circuits(topo, simple_modules))
    bias_variant = circuit.variant_map["bias_gen"]

    assert [p.name for p in bias_variant.ports] == [
        "ibias", "out1", "out2", "out3", "out4", "vdd", "gnd", "out5",
    ]
    assert len(bias_variant.devices) == 11

    bias_devices = {ref: dev for ref, dev in circuit.devices if ref.startswith("bias_gen_")}
    assert "bias_gen_mn6" in bias_devices  # cloned from mn5
    assert "bias_gen_mp5" in bias_devices  # cloned from mp4
    assert bias_devices["bias_gen_mn6"].terminals["d"] == "net_bias5"
    assert bias_devices["bias_gen_mp5"].terminals["d"] == "net_bias5"
    assert bias_devices["bias_gen_mp5"].terminals["g"] == "net_bias5"

    tail_devices = {ref: dev for ref, dev in circuit.devices if ref.startswith("tail_current_")}
    assert tail_devices["tail_current_m1"].terminals["d"] == "net_bias5"
    assert tail_devices["tail_current_m1"].terminals["g"] == "net_bias5"


@pytest.mark.parametrize(
    "variant_name,expected",
    [
        ("current_mirror_tail_pmos", True),
        ("current_mirror_tail_nmos", True),
        ("cascode_current_mirror_tail_pmos", True),
        ("cascode_current_mirror_tail_nmos", True),
        ("resistor_tail_vdd", False),
        ("resistor_tail_gnd", False),
    ],
)
def test_tail_current_needs_bias(variant_name, expected):
    modules = load_modules()
    variant = next(v for v in modules["tail_current"] if v.name == variant_name)
    assert tail_current_needs_bias(variant) is expected


@pytest.mark.parametrize(
    "load_needed,expected_rail",
    [
        (set(), 1),
        ({1}, 2),
        ({1, 2}, 3),
        ({1, 2, 3, 4}, 5),
    ],
)
def test_assign_tail_bias_rail(load_needed, expected_rail):
    assert assign_tail_bias_rail(load_needed) == expected_rail


@pytest.mark.parametrize(
    "variant_name,cloned_refs",
    [
        ("magic_battery_bias", {"mp6", "mn5"}),
        ("diode_connected_mosfet_bias", {"mn6", "mp5"}),
        ("resistor_bias", {"mp6", "r5"}),
    ],
)
def test_extend_bias_generation(variant_name, cloned_refs):
    """extend_bias_generation clones leg 4's devices onto out5 with
    incremented refs, appends an out5 output port, and leaves the original 9
    devices untouched."""
    modules = load_modules()
    variant = next(v for v in modules["bias_generation"] if v.name == variant_name)
    original_refs = {dev.ref for dev in variant.devices}

    extended = extend_bias_generation(variant)

    assert len(extended.devices) == 11
    assert [p.name for p in extended.ports][-1] == "out5"
    assert {p.name for p in extended.ports} == {
        "ibias", "out1", "out2", "out3", "out4", "out5", "vdd", "gnd",
    }

    new_refs = {dev.ref for dev in extended.devices} - original_refs
    assert new_refs == cloned_refs
    for dev in extended.devices:
        if dev.ref in cloned_refs:
            refs = {t for t in dev.terminals.values() if t.startswith("out")}
            assert refs == {"out5"}


@pytest.mark.parametrize(
    "bias_variant_name", ["diode_connected_mosfet_bias", "resistor_bias", "magic_battery_bias"]
)
def test_enumerate_circuits_assigns_tail_bias_rail_one_stage_simple_load(bias_variant_name):
    """one_stage_opamp + simple load (load_needed={}) + current_mirror_tail_nmos
    (needs bias): tail gets rail 1 (net_bias1); bias_gen is pruned to shared
    reference + leg 1 only."""
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

    assert [p.name for p in bias_variant.ports if p.name.startswith("out")] == ["out1"]
    assert len(bias_variant.devices) == 3  # shared ref + 1 leg

    tail_devices = {ref: dev for ref, dev in circuit.devices if ref.startswith("tail_current_")}
    assert tail_devices["tail_current_m1"].terminals["d"] == "net_bias1"
    assert tail_devices["tail_current_m1"].terminals["g"] == "net_bias1"


@pytest.mark.parametrize(
    "bias_variant_name", ["diode_connected_mosfet_bias", "resistor_bias", "magic_battery_bias"]
)
def test_enumerate_circuits_assigns_tail_bias_rail_two_stage_simple_load(bias_variant_name):
    """two_stage_opamp_single_ended + simple load (load_needed={1}, forced by
    second_stage) + current_mirror_tail_nmos: tail gets rail 2 (net_bias2),
    distinct from load/second_stage's rail 1."""
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

    assert [p.name for p in bias_variant.ports if p.name.startswith("out")] == ["out1", "out2"]
    assert len(bias_variant.devices) == 5  # shared ref + 2 legs

    tail_devices = {ref: dev for ref, dev in circuit.devices if ref.startswith("tail_current_")}
    assert tail_devices["tail_current_m1"].terminals["d"] == "net_bias2"


@pytest.mark.parametrize(
    "bias_variant_name", ["diode_connected_mosfet_bias", "resistor_bias", "magic_battery_bias"]
)
def test_enumerate_circuits_assigns_tail_bias_rail_overflow_to_rail_5(bias_variant_name):
    """one_stage_opamp + differential-output folded-cascode load (load_needed=
    {1,2,3,4}) + current_mirror_tail_nmos: tail gets the overflow rail 5
    (net_bias5), and bias_gen is extended with a fifth leg for all 3 variants."""
    modules = load_modules()
    topo = next(t for t in load_topologies() if t.name == "one_stage_opamp")
    simple_modules = {
        "input_pair": [v for v in modules["input_pair"] if v.name == "differential_pair_nmos"],
        "load": [v for v in modules["load"] if v.name == "folded_cascode_load_nmos_input_differential_output"],
        "tail_current": [v for v in modules["tail_current"] if v.name == "current_mirror_tail_nmos"],
        "bias_generation": [v for v in modules["bias_generation"] if v.name == bias_variant_name],
    }

    circuit = next(enumerate_circuits(topo, simple_modules))
    bias_variant = circuit.variant_map["bias_gen"]

    assert "out5" in {p.name for p in bias_variant.ports}
    assert len(bias_variant.devices) == 11  # 9 + 2 cloned

    tail_devices = {ref: dev for ref, dev in circuit.devices if ref.startswith("tail_current_")}
    assert tail_devices["tail_current_m1"].terminals["d"] == "net_bias5"
    assert tail_devices["tail_current_m1"].terminals["g"] == "net_bias5"


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
