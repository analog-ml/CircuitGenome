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


_C0001 = _ROOT / "circuits" / "two_stage_opamp_single_ended" / "circuit_0001_flat.ckt"


@pytest.mark.skipif(not (_C0110.exists() and _PTM_SPEC.exists()),
                    reason="ptm45 two-stage fixtures not present")
def test_size_ptm45_infeasible_verdict(capsys):
    """circuit_0110's cascode tail can't bias → INFEASIBLE verdict, no table."""
    main(["size", str(_C0110), "--topology", "two_stage_opamp_single_ended",
          "--spec", str(_PTM_SPEC), "--tech", "ptm45"])
    out = capsys.readouterr().out
    assert "INFEASIBLE" in out
    # the misleading metrics table is suppressed entirely
    assert "Performance metrics:" not in out
    assert "Open-loop gain" not in out
    # the bias reason is stated inline with the verdict
    assert "cascode tail" in out


@pytest.mark.skipif(not (_C0001.exists() and _PTM_SPEC.exists()),
                    reason="ptm45 two-stage fixtures not present")
def test_size_ptm45_marginal_verdict(capsys):
    """A biasing-but-underperforming circuit → MARGINAL with real metrics + ✗."""
    main(["size", str(_C0001), "--topology", "two_stage_opamp_single_ended",
          "--spec", str(_PTM_SPEC), "--tech", "ptm45"])
    out = capsys.readouterr().out
    assert "MARGINAL" in out
    assert "Performance metrics:" in out  # metrics are real → shown
    assert "Open-loop gain" in out
    assert "✗" in out                     # at least one failing margin


@pytest.mark.skipif(not _C0001.exists(), reason="two-stage fixture not present")
def test_size_feasible_verdict(capsys, tmp_path):
    """A relaxed spec the design meets → FEASIBLE with the ✓ table."""
    spec = tmp_path / "relaxed.yaml"
    spec.write_text(
        "vdd: 5.0\nvss: 0.0\nibias: 20.0e-6\ncl: 5.0e-12\n"
        "second_stage_current_ratio: 2.5\ngain_min_db: 20\ngbw_min_hz: 3.0e+5\n"
        "phase_margin_min_deg: 45\nslew_rate_min_vps: 1.0e+5\n")
    main(["size", str(_C0001), "--topology", "two_stage_opamp_single_ended",
          "--spec", str(spec), "--tech", "generic"])
    out = capsys.readouterr().out
    assert "Feasibility: FEASIBLE" in out
    assert "INFEASIBLE" not in out and "MARGINAL" not in out
    assert "Performance metrics:" in out


@pytest.mark.skipif(not (_C0110.exists() and _PTM_SPEC.exists()),
                    reason="ptm45 two-stage fixtures not present")
def test_size_ptm_without_lut_errors(capsys):
    """A PTM node without a gm/Id LUT exits cleanly instead of using Level-1."""
    with pytest.raises(SystemExit) as exc:
        main(["size", str(_C0110), "--topology", "two_stage_opamp_single_ended",
              "--spec", str(_PTM_SPEC), "--tech", "ptm32"])
    assert exc.value.code == 1
    assert "gm/Id LUT" in capsys.readouterr().err
