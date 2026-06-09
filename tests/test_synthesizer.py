import pytest
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


def test_load_topologies():
    topologies = load_topologies()
    names = [t.name for t in topologies]
    assert "two_stage_opamp_single_ended" in names
    assert "one_stage_opamp" in names


def test_enumerate_circuits_nonempty():
    modules = load_modules()
    topologies = load_topologies()
    topo = next(t for t in topologies if t.name == "two_stage_opamp_single_ended")
    circuits = list(enumerate_circuits(topo, modules))
    assert len(circuits) > 0


def test_enumerate_circuits_count():
    """2-stage single-ended: 5 input pairs × 6 loads × 3 tails × 3 bias × 3 comp × 3 second = 2430."""
    modules = load_modules()
    topologies = load_topologies()
    topo = next(t for t in topologies if t.name == "two_stage_opamp_single_ended")
    circuits = list(enumerate_circuits(topo, modules))
    assert len(circuits) == 2430


def test_flat_spice_structure():
    modules = load_modules()
    topologies = load_topologies()
    topo = next(t for t in topologies if t.name == "one_stage_opamp")

    # Use the simplest variants for a deterministic test
    simple_modules = {
        "input_pair": [v for v in modules["input_pair"] if v.name == "differential_pair_pmos"],
        "load": [v for v in modules["load"] if v.name == "resistor_load"],
        "tail_current": [v for v in modules["tail_current"] if v.name == "resistor_tail"],
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
        "load": [v for v in modules["load"] if v.name == "resistor_load"],
        "tail_current": [v for v in modules["tail_current"] if v.name == "resistor_tail"],
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
        "load": [v for v in modules["load"] if v.name == "resistor_load"],
        "tail_current": [v for v in modules["tail_current"] if v.name == "resistor_tail"],
        "bias_generation": [v for v in modules["bias_generation"] if v.name == "resistor_bias"],
    }

    circuit = next(enumerate_circuits(topo, simple_modules))
    spice = to_hierarchical_spice(circuit, name="test_opamp_hier")

    # Should have module subcircuit definitions
    assert ".subckt differential_pair_pmos" in spice
    assert ".subckt resistor_load" in spice
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
