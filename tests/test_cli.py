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


_ROOT = Path(__file__).resolve().parent.parent
_C0110 = _ROOT / "circuits" / "two_stage_opamp_single_ended" / "circuit_0110_flat.ckt"
_PTM_SPEC = _ROOT / "examples" / "two_stage_se_specs" / "spec_ptm45.yaml"


@pytest.mark.skipif(not (_C0110.exists() and _PTM_SPEC.exists()),
                    reason="ptm45 two-stage fixtures not present")
def test_size_ptm45_infeasible_marks_metrics_unreliable(capsys):
    """circuit_0110's cascode tail can't bias → metrics tagged [unreliable]."""
    main(["size", str(_C0110), "--topology", "two_stage_opamp_single_ended",
          "--spec", str(_PTM_SPEC), "--tech", "ptm45"])
    out = capsys.readouterr().out
    assert "UNRELIABLE" in out
    assert "[unreliable]" in out
    assert "✓" not in out  # no pass-fail ticks when the bias is infeasible


@pytest.mark.skipif(not (_C0110.exists() and _PTM_SPEC.exists()),
                    reason="ptm45 two-stage fixtures not present")
def test_size_ptm_without_lut_errors(capsys):
    """A PTM node without a gm/Id LUT exits cleanly instead of using Level-1."""
    with pytest.raises(SystemExit) as exc:
        main(["size", str(_C0110), "--topology", "two_stage_opamp_single_ended",
              "--spec", str(_PTM_SPEC), "--tech", "ptm32"])
    assert exc.value.code == 1
    assert "gm/Id LUT" in capsys.readouterr().err
