from circuitgenome.synthesizer.loader import load_modules, load_topologies
from circuitgenome.synthesizer.synthesizer import enumerate_circuits
from circuitgenome.synthesizer.netlist import to_flat_spice
from circuitgenome.recognizer.netlist_parser import parse
from circuitgenome.recognizer.subcircuit_recognizer import recognize
from circuitgenome.recognizer.functional_block_recognizer import assign_slots


def _one_stage_combo():
    """The fixed combo for this slice: differential_pair_nmos / active_load_pmos /
    current_mirror_tail_nmos / diode_connected_mosfet_bias, all polarity-compatible
    and all-MOSFET so Layer 0 only needs to handle MOSFET device lines."""
    modules = load_modules()
    topology = next(t for t in load_topologies() if t.name == "one_stage_opamp")
    simple_modules = {
        "input_pair": [v for v in modules["input_pair"] if v.name == "differential_pair_nmos"],
        "load": [v for v in modules["load"] if v.name == "active_load_pmos"],
        "tail_current": [v for v in modules["tail_current"] if v.name == "current_mirror_tail_nmos"],
        "bias_generation": [v for v in modules["bias_generation"] if v.name == "diode_connected_mosfet_bias"],
        "cmfb": modules["cmfb"],
        "compensation": modules["compensation"],
        "second_stage": modules["second_stage"],
    }
    circuit = next(enumerate_circuits(topology, simple_modules))
    return circuit, topology


def test_round_trip_one_stage_opamp():
    circuit, topology = _one_stage_combo()

    spice = to_flat_spice(circuit)
    parsed = parse(spice)

    sr_result = recognize(parsed)
    assert sr_result.unrecognized_devices == []

    fbr_result = assign_slots(sr_result, topology)

    for slot_name, variant in circuit.variant_map.items():
        assert fbr_result.slot_assignments[slot_name].pattern_name == variant.name
