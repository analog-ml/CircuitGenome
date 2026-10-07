"""Tests for tools/gen_prediction_error.py — the fast, SPICE-free parts.

The ``run`` command itself (hours of ngspice) is exercised by regenerating
the docs page, not here.
"""
import csv
import importlib.util
import json
from pathlib import Path

import pytest

_TOOL = Path(__file__).resolve().parent.parent / "tools" / "gen_prediction_error.py"
_spec = importlib.util.spec_from_file_location("gen_prediction_error", _TOOL)
pe = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(pe)


def _row(template="one_stage_opamp", index=1, outcome="simulated",
         open_loop_measurable=True, pred=None, meas=None):
    row = {"template": template, "index": index, "variants": "load=x;tail=y",
           "outcome": outcome, "open_loop_measurable": open_loop_measurable}
    for k in pe.METRICS:
        row[f"pred_{k}"] = (pred or {}).get(k)
        row[f"meas_{k}"] = (meas or {}).get(k)
    return row


def test_sample_is_deterministic_sorted_and_per_template():
    a = pe.sample_indices(1000, 10, seed=0, template="t1")
    assert a == pe.sample_indices(1000, 10, seed=0, template="t1")
    assert a == sorted(a) and len(set(a)) == 10
    assert all(1 <= i <= 1000 for i in a)
    assert a != pe.sample_indices(1000, 10, seed=0, template="t2")
    assert a != pe.sample_indices(1000, 10, seed=1, template="t1")


def test_small_template_is_taken_whole():
    assert pe.sample_indices(54, 100, seed=0, template="t") == list(range(1, 55))


def test_prediction_error_is_measured_minus_predicted():
    assert pe.prediction_error("gain_db", 80.0, 74.0) == pytest.approx(-6.0)
    assert pe.prediction_error("phase_margin_deg", 60.0, 65.0) == pytest.approx(5.0)
    # Decade-spanning metrics are relative, in percent of the prediction.
    assert pe.prediction_error("gbw_hz", 2e6, 1e6) == pytest.approx(-50.0)
    assert pe.prediction_error("power_w", 0.0, 1e-3) is None


def test_error_of_excludes_unsimulated_and_missing_pairs():
    pair = {"gain_db": 80.0}
    assert pe.error_of(_row(pred=pair, meas={"gain_db": 70.0}), "gain_db") == -10.0
    assert pe.error_of(_row(outcome="bias_infeasible_sizer", pred=pair,
                            meas={"gain_db": 70.0}), "gain_db") is None
    assert pe.error_of(_row(pred=pair), "gain_db") is None


def test_open_loop_ceiling_only_drops_gain_gbw_and_phase_margin():
    row = _row(open_loop_measurable=False,
               pred={"gain_db": 130.0, "gbw_hz": 1e6, "slew_rate_vps": 1e6},
               meas={"gain_db": 0.0, "gbw_hz": 1e3, "slew_rate_vps": 2e6})
    assert pe.error_of(row, "gain_db") is None
    assert pe.error_of(row, "gbw_hz") is None
    assert pe.error_of(row, "slew_rate_vps") == pytest.approx(100.0)


def test_fully_differential_cmrr_and_psrr_are_excluded():
    pair = dict(pred={"cmrr_db": 40.0, "gain_db": 80.0},
                meas={"cmrr_db": 280.0, "gain_db": 78.0})
    fd = _row(template="two_stage_opamp_fully_differential", **pair)
    se = _row(template="two_stage_opamp_single_ended", **pair)
    assert pe.error_of(fd, "cmrr_db") is None
    assert pe.error_of(fd, "gain_db") == pytest.approx(-2.0)
    assert pe.error_of(se, "cmrr_db") == pytest.approx(240.0)


def test_stats_median_spread_and_signed_worst():
    st = pe.stats([-10.0, 1.0, 2.0, 3.0, 4.0])
    assert st["n"] == 5
    assert st["median"] == 2.0
    assert st["p10"] == pytest.approx(-5.6)
    assert st["p90"] == pytest.approx(3.6)
    assert st["worst"] == -10.0
    assert pe.stats([]) is None


def test_coverage_counts_every_outcome():
    rows = [_row(pred={"gain_db": 1.0}), _row(open_loop_measurable=False),
            _row(outcome="sizing_failed"), _row(outcome="bias_infeasible_spice")]
    c = pe.coverage(rows)
    assert c["sampled"] == 4 and c["simulated"] == 2
    assert c["sizing_failed"] == 1 and c["bias_infeasible_spice"] == 1
    assert c["no_predicted_gain"] == 1 and c["above_ceiling"] == 1


def test_render_round_trips_csv_into_tables_figures_and_explorer(tmp_path):
    pytest.importorskip("matplotlib")
    results = tmp_path / "prediction_error" / "results"
    results.mkdir(parents=True)
    rows = [_row(index=i, pred={"gain_db": 80.0, "gbw_hz": 1e6},
                 meas={"gain_db": 80.0 - i, "gbw_hz": 1.1e6})
            for i in (1, 2, 3)]
    rows.append(_row(index=4, outcome="sizing_failed"))
    with (results / "one_stage_opamp.csv").open("w", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=pe.COLUMNS, extrasaction="ignore")
        writer.writeheader()
        writer.writerows(rows)
    info = {"tech": "gf180mcu", "spec": "spec.yaml", "n_per_template": 4,
            "seed": 0, "commit": "abc1234", "ngspice": "ngspice-44.2",
            "date": "2026-10-07"}
    run_info = tmp_path / "prediction_error" / "run.json"
    run_info.write_text(json.dumps(info))
    tables = tmp_path / "prediction_error" / "tables.inc"
    explorer = tmp_path / "_extra" / "explorer" / "index.html"

    pe.render(results, tables, explorer, run_info, docs=tmp_path)

    text = tables.read_text()
    assert text.startswith(".. DO NOT EDIT")
    assert "commit ``abc1234``" in text
    assert ".. image:: prediction_error/gain_db.svg" in text
    # gain errors -1, -2, -3 dB → median -2.0, worst -3.0; GBW +10 %.
    assert "     - -2.0\n" in text and "     - -3.0\n" in text
    assert "     - +10.0\n" in text
    for k in pe.METRICS:
        assert (tmp_path / "prediction_error" / f"{k}.svg").exists()
    html = explorer.read_text()
    data = json.loads(html.split("const D = ", 1)[1].split(";\n", 1)[0])
    assert len(data["points"]["gain_db"]) == 3
    assert data["points"]["gain_db"][0][:2] == ["one_stage_opamp", 1]
