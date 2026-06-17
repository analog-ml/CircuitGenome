import pytest
from pathlib import Path

from circuitgenome.cli import main
from circuitgenome.synthesizer.loader import load_modules, load_topologies
from circuitgenome.synthesizer.synthesizer import enumerate_circuits
from circuitgenome.synthesizer.netlist import to_flat_spice


@pytest.fixture(scope="module")
def sample_netlist(tmp_path_factory):
    modules = load_modules()
    topology = next(t for t in load_topologies() if t.name == "one_stage_opamp")
    simple_modules = {
        "input_pair":      [v for v in modules["input_pair"]      if v.name == "differential_pair_pmos"],
        "load":            [v for v in modules["load"]            if v.name == "active_load_nmos"],
        "tail_current":    [v for v in modules["tail_current"]    if v.name == "current_mirror_tail_pmos"],
        "bias_generation": [v for v in modules["bias_generation"] if v.name == "diode_connected_mosfet_bias"],
    }
    circuit = next(enumerate_circuits(topology, simple_modules))
    spice = to_flat_spice(circuit)
    path = tmp_path_factory.mktemp("cli") / "circuit.ckt"
    path.write_text(spice)
    return path, topology


def test_recognize_sr_only(sample_netlist, capsys):
    path, _ = sample_netlist
    main(["recognize", str(path)])
    out = capsys.readouterr().out
    assert "Recognized structures" in out
    assert "differential_pair_pmos" in out
    assert "Unrecognized devices: none" in out


def test_recognize_with_topology(sample_netlist, capsys):
    path, topology = sample_netlist
    main(["recognize", str(path), "--topology", topology.name])
    out = capsys.readouterr().out
    assert "Slot assignments" in out
    assert "differential_pair_pmos" in out
    assert "(unassigned)" not in out


def test_recognize_unknown_topology(sample_netlist):
    path, _ = sample_netlist
    with pytest.raises(SystemExit) as exc:
        main(["recognize", str(path), "--topology", "nonexistent_topology"])
    assert exc.value.code == 1
