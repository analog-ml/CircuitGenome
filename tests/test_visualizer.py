import pytest

from circuitgenome.synthesizer.cmfb_compatibility import CANONICAL_CMFB_VARIANT
from circuitgenome.synthesizer.loader import load_modules, load_topologies
from circuitgenome.synthesizer.synthesizer import build_circuit, enumerate_circuits
from circuitgenome.synthesizer.tail_current_compatibility import CANONICAL_TAIL_CURRENT_VARIANT
from circuitgenome.visualizer.graph import SUPPLY_NETS, explain_incompatibility, topology_to_graph


@pytest.fixture(scope="module")
def modules():
    return load_modules()


@pytest.fixture(scope="module")
def topologies(modules):
    return {t.name: t for t in load_topologies()}


@pytest.fixture(scope="module")
def by_name(modules):
    return {v.name: v for variants in modules.values() for v in variants}


def _variant_map(by_name, names: dict[str, str]) -> dict:
    return {slot: by_name[name] for slot, name in names.items()}


# One-stage and two-stage-fully-differential variant maps reused across tests.
ONE_STAGE_VALID = {
    "input_pair": "differential_pair_pmos",
    "load": "resistor_load_gnd",
    "tail_current": "current_mirror_tail_pmos",
}
TWO_STAGE_FD_VALID = {
    "input_pair": "differential_pair_pmos",
    "load": "resistor_load_gnd",
    "tail_current": "current_mirror_tail_pmos",
    "cmfb": CANONICAL_CMFB_VARIANT,
    "comp_p": "miller_cap",
    "comp_n": "miller_cap",
    "second_stage_p": "common_source",
    "second_stage_n": "common_source",
}


def test_topology_to_graph_one_node_per_slot(topologies, by_name):
    topo = topologies["one_stage_opamp"]
    variant_map = _variant_map(by_name, ONE_STAGE_VALID)

    graph = topology_to_graph(topo, variant_map)

    assert len(graph.nodes) == len(topo.slots)
    by_id = {n.id: n for n in graph.nodes}
    for slot in topo.slots:
        node = by_id[slot.name]
        assert node.category == slot.category
        if slot.category == "bias_generation":
            # not in variant_map (constructed by build_circuit): placeholder
            assert node.variant_name == ""
            assert node.label == "(bias_generation)"
        else:
            assert node.variant_name == variant_map[slot.name].name
            assert node.label == variant_map[slot.name].display_name
        assert node.is_pruned is False


def test_build_edges_one_stage_opamp_no_self_loops(topologies, by_name):
    topo = topologies["one_stage_opamp"]
    variant_map = _variant_map(by_name, ONE_STAGE_VALID)

    graph = topology_to_graph(topo, variant_map)

    assert len(graph.edges) == 9  # incl. the static rail-8 tail bias_casc edge
    assert not any(e.source == e.target for e in graph.edges)

    diff1_edges = [e for e in graph.edges if e.net == "net_diff1"]
    assert len(diff1_edges) == 1
    edge = diff1_edges[0]
    assert {edge.source, edge.target} == {"input_pair", "load"}
    assert {edge.source_port, edge.target_port} == {"out1", "in1"}


def test_build_edges_two_stage_fd_fanout(topologies, by_name):
    topo = topologies["two_stage_opamp_fully_differential"]
    variant_map = _variant_map(by_name, TWO_STAGE_FD_VALID)

    graph = topology_to_graph(topo, variant_map)

    for net in ("net_loadout1", "net_loadout2"):
        edges = [e for e in graph.edges if e.net == net]
        assert len(edges) == 6, f"{net}: expected 6 edges, got {len(edges)}"


def test_supply_nets_excluded_from_edges(topologies, modules):
    for topo in topologies.values():
        variant_map = {
            slot.name: modules[slot.category][0]
            for slot in topo.slots
            if modules.get(slot.category)
        }
        graph = topology_to_graph(topo, variant_map)
        nets = {e.net for e in graph.edges}
        assert nets.isdisjoint(SUPPLY_NETS)


def test_is_pruned_for_placeholder_variants(topologies, by_name):
    one_stage = topologies["one_stage_opamp"]
    variant_map = _variant_map(by_name, {
        "input_pair": "inverter_based_input",
        "load": "resistor_load_vdd",
        "tail_current": CANONICAL_TAIL_CURRENT_VARIANT,
    })
    circuit = build_circuit(one_stage, variant_map)
    assert circuit is not None
    nodes = {n.category: n for n in topology_to_graph(one_stage, circuit.variant_map).nodes}
    assert nodes["tail_current"].is_pruned
    assert nodes["input_pair"].is_pruned is False

    two_stage_fd = topologies["two_stage_opamp_fully_differential"]
    variant_map2 = _variant_map(by_name, TWO_STAGE_FD_VALID)
    circuit2 = build_circuit(two_stage_fd, variant_map2)
    assert circuit2 is not None
    nodes2 = {n.category: n for n in topology_to_graph(two_stage_fd, circuit2.variant_map).nodes}
    assert nodes2["cmfb"].is_pruned


def test_build_circuit_returns_none_for_polarity_mismatch(topologies, by_name):
    topo = topologies["one_stage_opamp"]
    variant_map = _variant_map(by_name, {
        "input_pair": "differential_pair_nmos",
        "load": "active_load_nmos",
        "tail_current": "current_mirror_tail_nmos",
    })
    assert build_circuit(topo, variant_map) is None


def test_build_circuit_returns_none_for_output_cardinality_mismatch(topologies, by_name):
    topo = topologies["one_stage_opamp"]  # single_ended
    variant_map = _variant_map(by_name, {
        "input_pair": "differential_pair_nmos",
        "load": "folded_cascode_load_nmos_input_differential_output",
        "tail_current": "current_mirror_tail_nmos",
    })
    assert build_circuit(topo, variant_map) is None


def test_build_circuit_matches_enumerate_circuits(topologies, modules, by_name):
    topo = topologies["one_stage_opamp"]
    variant_map = _variant_map(by_name, ONE_STAGE_VALID)

    circuit = build_circuit(topo, variant_map)
    expected = next(
        c for c in enumerate_circuits(topo, modules)
        if all(c.variant_map[k].name == v.name for k, v in variant_map.items())
    )

    assert circuit.name == expected.name
    assert circuit.devices == expected.devices


def test_explain_incompatibility(topologies, by_name):
    topo = topologies["one_stage_opamp"]

    assert explain_incompatibility(topo, _variant_map(by_name, ONE_STAGE_VALID)) == []

    polarity_bad = _variant_map(by_name, {
        "input_pair": "differential_pair_nmos",
        "load": "active_load_nmos",
        "tail_current": "current_mirror_tail_nmos",
    })
    reasons = explain_incompatibility(topo, polarity_bad)
    assert any("Polarity mismatch" in r for r in reasons)

    cardinality_bad = _variant_map(by_name, {
        "input_pair": "differential_pair_nmos",
        "load": "folded_cascode_load_nmos_input_differential_output",
        "tail_current": "current_mirror_tail_nmos",
    })
    reasons2 = explain_incompatibility(topo, cardinality_bad)
    assert any("output_cardinality" in r for r in reasons2)

    stage_bad = _variant_map(by_name, {
        "input_pair": "differential_pair_nmos",
        "load": "resistor_load_vdd",
        "tail_current": "current_mirror_tail_nmos",
        "compensation": "miller_cap",
        "second_stage": "common_source",
    })
    reasons3 = explain_incompatibility(
        topologies["two_stage_opamp_single_ended"], stage_bad
    )
    assert any("stage-interface" in r for r in reasons3)

    load_branch_bad = _variant_map(by_name, {
        "input_pair": "differential_pair_nmos",
        "load": "current_source_load_pmos",
        "tail_current": "current_mirror_tail_nmos",
    })
    reasons4 = explain_incompatibility(topo, load_branch_bad)
    assert any("untapped" in r for r in reasons4)


def test_enumerate_circuits_count_unchanged_after_refactor(modules, topologies):
    """Sanity check that build_circuit (shared with enumerate_circuits in
    synthesizer.py) applies the same filter pipeline: 48 effective
    input_pair/load/tail_current combos (current_source_load_* excluded
    from single-ended, issue #112; inverter_based_input parked as
    unsupported, issue #113), each with its one constructed bias
    generator."""
    assert len(list(enumerate_circuits(topologies["one_stage_opamp"], modules))) == 48
