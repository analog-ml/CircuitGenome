"""Tests for the Designer module (circuitgenome/designer)."""
from __future__ import annotations

import json
from itertools import islice

import pytest

from circuitgenome.designer import design
from circuitgenome.designer.designer import _evaluate_candidate, _margins
from circuitgenome.sizer.shared.loader import load_tech
from circuitgenome.sizer.shared.models import SizingSpec
from circuitgenome.sizer.shared.spice_sim import ngspice_available
from circuitgenome.synthesizer import enumerate_circuits, to_flat_spice
from circuitgenome.synthesizer.loader import load_modules, load_topologies


# ---------------------------------------------------------------------------
# Margin / acceptance logic (no ngspice needed)
# ---------------------------------------------------------------------------

def _spec(**kw):
    return SizingSpec(vdd=3.3, vss=0.0, ibias=20e-6, cl=5e-12, **kw)


def test_margins_min_and_max_specs():
    spec = _spec(gain_min_db=50, power_max_w=1e-3)
    m = _margins({"gain_db": 60.0, "power_w": 5e-4}, spec)
    assert m["gain_db"] == pytest.approx(0.2)   # (60-50)/50
    assert m["power_w"] == pytest.approx(0.5)   # (1m-0.5m)/1m


def test_margins_failing_spec_is_negative():
    spec = _spec(gbw_min_hz=2e6)
    m = _margins({"gbw_hz": 1e6}, spec)
    assert m["gbw_hz"] == pytest.approx(-0.5)


def test_margins_unmeasured_metric_is_skipped_not_failed():
    # A constrained metric ngspice returned None for must not appear (it is
    # unverified, not failed) — and unconstrained metrics never appear.
    spec = _spec(slew_rate_min_vps=1e6, gain_min_db=40)
    m = _margins({"slew_rate_vps": None, "gain_db": 50.0, "gbw_hz": 1e6}, spec)
    assert "slew_rate_vps" not in m
    assert "gbw_hz" not in m
    assert m == {"gain_db": pytest.approx(0.25)}


# ---------------------------------------------------------------------------
# Analytic gain gate (issue #125) — no ngspice needed
# ---------------------------------------------------------------------------

def _sizeable_item(topo, spec, tech):
    """First enumerated candidate that sizes GMID-feasible, as a worker item."""
    from circuitgenome.recognizer import assign_slots, parse, recognize
    from circuitgenome.sizer import size_circuit
    modules = load_modules()
    for i, circuit in enumerate(islice(enumerate_circuits(topo, modules), 60), 1):
        text = to_flat_spice(circuit, name=f"circuit_{i:04d}")
        parsed = parse(text)
        fbr = assign_slots(recognize(parsed), topo)
        result = size_circuit(parsed, recognize(parsed), fbr, topo, tech, spec)
        if result.solver_status == "GMID" and result.bias_feasible:
            variants = {s: v.name for s, v in circuit.variant_map.items() if v}
            return (i, text, variants)
    return None


def test_analytic_gain_gate_skips_spice(monkeypatch):
    """A candidate whose analytic gain is hopeless against the spec is
    rejected as spec_failed before any SPICE call (issue #125, defect 2)."""
    topo = next(t for t in load_topologies()
                if t.name == "two_stage_opamp_single_ended")
    tech = load_tech("gf180mcu")
    spec = _spec(second_stage_current_ratio=2.0, gain_min_db=200)
    item = _sizeable_item(topo, spec, tech)
    if item is None:
        pytest.skip("no GMID-feasible candidate in the first 60")

    monkeypatch.setattr("circuitgenome.designer.designer.check_bias_soundness",
                        lambda *a, **k: (True, None))

    def _no_spice(*a, **k):
        raise AssertionError("simulate_metrics must not run for a "
                             "gate-rejected candidate")
    monkeypatch.setattr("circuitgenome.designer.designer.simulate_metrics",
                        _no_spice)

    out = _evaluate_candidate(item, topo, tech, spec)
    assert out.stage == "spec_failed"
    assert out.detail.startswith("analytic gain_db")


def test_analytic_gain_gate_within_band_reaches_spice(monkeypatch):
    """A candidate inside the tolerance band still goes to SPICE — the
    analytic model is never the authority on accepts."""
    topo = next(t for t in load_topologies()
                if t.name == "two_stage_opamp_single_ended")
    tech = load_tech("gf180mcu")
    spec = _spec(second_stage_current_ratio=2.0, gain_min_db=10)
    item = _sizeable_item(topo, spec, tech)
    if item is None:
        pytest.skip("no GMID-feasible candidate in the first 60")

    monkeypatch.setattr("circuitgenome.designer.designer.check_bias_soundness",
                        lambda *a, **k: (True, None))
    calls = []

    def _fake_sim(*a, **k):
        calls.append(1)
        return {"gain_db": 60.0, "notes": []}
    monkeypatch.setattr("circuitgenome.designer.designer.simulate_metrics",
                        _fake_sim)
    monkeypatch.setattr("circuitgenome.designer.designer.sized_netlist",
                        lambda *a, **k: "* sized")

    out = _evaluate_candidate(item, topo, tech, spec)
    assert calls, "simulate_metrics should have run"
    assert out.stage == "accepted"


# ---------------------------------------------------------------------------
# Input validation
# ---------------------------------------------------------------------------

def test_design_requires_ngspice(monkeypatch, tmp_path):
    monkeypatch.setattr("circuitgenome.designer.designer.ngspice_available",
                        lambda: False)
    with pytest.raises(RuntimeError, match="ngspice"):
        design(_spec(), tmp_path, templates=["one_stage_opamp"])


@pytest.mark.skipif(not ngspice_available(), reason="ngspice not installed")
def test_design_rejects_unknown_template(tmp_path):
    with pytest.raises(ValueError, match="unknown template"):
        design(_spec(), tmp_path, templates=["no_such_template"])


@pytest.mark.skipif(not ngspice_available(), reason="ngspice not installed")
def test_design_rejects_tech_without_gmid_lut(tmp_path):
    with pytest.raises(ValueError, match="gm/Id LUT"):
        design(_spec(), tmp_path, templates=["one_stage_opamp"], tech="generic")


# ---------------------------------------------------------------------------
# End-to-end (gf180mcu + ngspice)
# ---------------------------------------------------------------------------

# With the bias flavor filter, enumeration indices 48+ of the two-stage SE
# template are the active-load variants that bias on gf180mcu; a small limit
# stays in the resistor-load range, which is enough to exercise the full
# reject/accept machinery.
_TOPO = "two_stage_opamp_single_ended"


@pytest.mark.skipif(not ngspice_available(), reason="ngspice not installed")
def test_design_end_to_end_loose_spec(tmp_path):
    # Loose spec: metrics only need to exist and clear trivial bars.  The
    # limit reaches the active-load variants (indices 48+), which measure a
    # healthy CMRR — the resistor-tail ones before them are now correctly
    # rejected on the measured-CMRR gate (a resistor tail rejects poorly).
    spec = _spec(second_stage_current_ratio=2.5, gain_min_db=40,
                 gbw_min_hz=5e5, phase_margin_min_deg=45, power_max_w=2e-3,
                 cmrr_min_db=20)
    report = design(spec, tmp_path, templates=[_TOPO], limit=100, workers=2)

    st = report.stats[_TOPO]
    assert st.enumerated == 100
    assert st.enumerated == (st.accepted + st.sizing_failed
                             + st.bias_infeasible + st.spec_failed
                             + st.unverified + st.errors)
    assert st.errors == 0
    # Every rejection carries a (normalized) reason in the histogram.
    assert sum(st.rejection_reasons.values()) == st.enumerated - st.accepted
    # Acceptance is strict: constrained specs are measured on every solution.
    for sol in report.solutions:
        assert sol.metrics.get("gain_db") is not None
        assert sol.metrics.get("cmrr_db") is not None
        assert isinstance(sol.notes, list)
    # The CMRR bench is exercised and gated: some accepted circuit measured
    # it, and every measured CMRR margin is non-negative.
    measured = [s for s in report.solutions
                if s.metrics.get("cmrr_db") is not None]
    assert measured
    assert all(s.margins["cmrr_db"] >= 0 for s in measured)
    assert report.runtime_s > 0

    assert len(report.solutions) == st.accepted
    for sol in report.solutions:
        assert sol.topology == _TOPO
        assert all(m >= 0 for m in sol.margins.values())
        path = tmp_path / _TOPO / f"{sol.name}_sized.ckt"
        assert str(path) == sol.netlist_path and path.exists()
        text = path.read_text()
        assert text.lstrip().startswith(".subckt") and "W=" in text

    if report.solutions:  # best points exist iff there are solutions
        assert "most_robust" in report.best_points
        robust = report.best_points["most_robust"]
        assert robust.worst_margin == max(s.worst_margin for s in report.solutions)

    data = json.loads((tmp_path / "report.json").read_text())
    assert set(data) == {"spec", "tech", "unverified_specs", "runtime_s",
                         "templates", "best_points", "solutions"}
    assert data["tech"] == "gf180mcu_3v3"
    assert len(data["solutions"]) == st.accepted


@pytest.mark.skipif(not ngspice_available(), reason="ngspice not installed")
def test_design_impossible_spec_yields_no_solutions(tmp_path):
    spec = _spec(gain_min_db=200)  # physically impossible
    report = design(spec, tmp_path, templates=[_TOPO], limit=5)
    assert report.solutions == []
    assert report.best_points == {}
    st = report.stats[_TOPO]
    assert st.enumerated == 5 and st.accepted == 0
    assert (tmp_path / "report.json").exists()
    assert not (tmp_path / _TOPO).exists()  # no solutions → no netlist folder


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def test_cli_design_requires_topology_or_all(tmp_path, capsys):
    from circuitgenome.cli import main
    with pytest.raises(SystemExit):
        main(["design", "--spec", "spec.yaml", "-o", str(tmp_path)])


def test_cli_design_errors_without_ngspice(tmp_path, monkeypatch, capsys):
    from circuitgenome.cli import main
    spec = tmp_path / "spec.yaml"
    spec.write_text("vdd: 3.3\nvss: 0.0\nibias: 2.0e-5\ncl: 5.0e-12\n")
    monkeypatch.setattr("circuitgenome.designer.designer.ngspice_available",
                        lambda: False)
    with pytest.raises(SystemExit) as exc:
        main(["design", "--spec", str(spec), "--topology", "one_stage_opamp",
              "-o", str(tmp_path / "out")])
    assert exc.value.code == 1
    assert "ngspice" in capsys.readouterr().err


@pytest.mark.skipif(not ngspice_available(), reason="ngspice not installed")
def test_cli_design_summary_output(tmp_path, capsys):
    from circuitgenome.cli import main
    spec = tmp_path / "spec.yaml"
    spec.write_text("vdd: 3.3\nvss: 0.0\nibias: 2.0e-5\ncl: 5.0e-12\n"
                    "gain_min_db: 200\n")
    main(["design", "--spec", str(spec), "--topology", _TOPO,
          "--limit", "3", "-o", str(tmp_path / "out")])
    out = capsys.readouterr().out
    assert "Design summary" in out
    assert _TOPO in out
    assert "0/3 circuits meet the spec" in out
    assert "report.json" in out
